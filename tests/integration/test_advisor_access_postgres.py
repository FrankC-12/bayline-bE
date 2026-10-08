"""Exercise the advisor's client and ODS endpoints with real permissions."""

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients import router as clients
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders import router as orders
from app.modules.users.models import User, UserModulePermission


def promote_legacy_advisor(connection):
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/a7d84c219f60_advisor_client_write_access.py"
    )
    spec = importlib.util.spec_from_file_location("advisor_access_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


@pytest.mark.asyncio
async def test_legacy_advisor_can_register_clients_and_read_orders(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        role = Role(name="Asesor de Servicios", slug="asesor", scope=RoleScope.FILIAL)
        db.add(role)
        await db.flush()
        db.add_all(
            [
                RoleModulePermission(
                    role_id=role.id, module_id="clientes-vehiculos", access=AccessLevel.VER
                ),
                RoleModulePermission(
                    role_id=role.id, module_id="asesor-servicios", access=AccessLevel.EDITAR
                ),
            ]
        )
        user = User(
            full_name="Asesor QA",
            email=f"{uuid.uuid4()}@example.com",
            role_id=role.id,
            filial_id=ctx.filial_id,
        )
        db.add(user)
        await db.commit()
        current = CurrentUser(
            user_id=user.id,
            email=user.email,
            role_id=role.id,
            role_slug="asesor",
            scope="filial",
            filial_id=ctx.filial_id,
            holding_id=None,
        )
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(clients.router, prefix="/api/v1")
        app.include_router(orders.router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: current
        app.dependency_overrides[get_db] = lambda: db
        payload = dict(
            filial_id=str(ctx.filial_id),
            full_name="Cliente QA",
            client_type="particular",
            document_type="V",
            document_number="87654321",
            phone_primary="04141234567",
            address="Caracas",
            vehicles=[dict(brand="Toyota", model="Hilux")],
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.post("/api/v1/clients", json=payload)).status_code == 403
            await (await db.connection()).run_sync(promote_legacy_advisor)
            await db.commit()
            response = await http.post("/api/v1/clients", json=payload)
            assert response.status_code == 201, response.text
            assert response.json()["vehicles"][0]["plate"] is None
            db.expunge_all()
            response = await http.get(
                f"/api/v1/service-orders?filial_id={ctx.filial_id}&view=active"
            )
            assert response.status_code == 200, response.text
            assert str(ctx.order_id) in [row["id"] for row in response.json()]
            db.expunge_all()
            response = await http.get(f"/api/v1/service-orders/{ctx.order_id}")
            assert response.status_code == 200, response.text
            assert response.json()["order_type"]["name"]
            assert (
                await http.get(f"/api/v1/service-orders?filial_id={uuid.uuid4()}")
            ).status_code == 403
            assert (
                await http.post("/api/v1/clients", json={**payload, "filial_id": str(uuid.uuid4())})
            ).status_code == 403
            db.add(
                UserModulePermission(
                    user_id=current.user_id,
                    module_id="clientes-vehiculos",
                    access=AccessLevel.SIN_ACCESO,
                )
            )
            await db.commit()
            await (await db.connection()).run_sync(promote_legacy_advisor)
            await db.commit()
            assert (await http.post("/api/v1/clients", json=payload)).status_code == 403
        # The role is global; keep this test independent of test order.
        await db.execute(delete(User).where(User.id == current.user_id))
        await db.execute(delete(Role).where(Role.id == current.role_id))
        await db.commit()
