"""Holding can administer an explicitly selected own filial, never another tenant."""

import uuid
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.core.security import decode_access_token
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import AuthService
from app.modules.filiales.models import Filial
from app.modules.holdings.models import Holding
from app.modules.holdings.control_models import HoldingAuditEvent
from app.modules.roles.enums import RoleScope, AccessLevel
from app.modules.roles.models import Role
from app.modules.users.models import User
from app.modules.users.enums import UserStatus
from app.modules.users.router import router as user_router
from app.modules.warehouse.router import router as warehouse_router


@pytest.mark.asyncio
async def test_holding_context_is_scoped_renewable_and_can_manage_users(billing_db):
    ctx = billing_db
    app = FastAPI()
    for router in [auth_router, user_router, warehouse_router]:
        app.include_router(router, prefix="/api/v1")
    register_exception_handlers(app)
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        holding_role = (
            await db.execute(select(Role).where(Role.slug == "holding-admin"))
        ).scalar_one_or_none()
        if holding_role is None:
            holding_role = Role(name="Holding", slug="holding-admin", scope=RoleScope.HOLDING)
            db.add(holding_role)
        worker_role = Role(name="Worker", slug=uuid.uuid4().hex, scope=RoleScope.FILIAL)
        other_holding = Holding(name="Other", slug=uuid.uuid4().hex)
        db.add_all([worker_role, other_holding])
        await db.flush()
        foreign = Filial(name="Foreign", slug=uuid.uuid4().hex, holding_id=other_holding.id)
        sibling = Filial(name="Sibling", slug=uuid.uuid4().hex, holding_id=filial.holding_id)
        db.add_all([foreign, sibling])
        await db.flush()
        owner = User(
            full_name="Holding admin",
            email=f"{uuid.uuid4().hex}@test.com",
            role_id=holding_role.id,
            holding_id=filial.holding_id,
            status=UserStatus.ACTIVO,
        )
        worker = User(
            full_name="Employee",
            email=f"{uuid.uuid4().hex}@test.com",
            role_id=worker_role.id,
            holding_id=filial.holding_id,
            filial_id=filial.id,
            status=UserStatus.ACTIVO,
        )
        db.add_all([owner, worker])
        await db.commit()
        tokens = await AuthService(db)._token_response(owner)

        async def database():
            yield db

        app.dependency_overrides[get_db] = database
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {tokens.access_token}"},
        ) as client:
            result = await client.post(
                "/api/v1/auth/holding-context", json={"filial_id": str(foreign.id)}
            )
            assert result.status_code == 403
            result = await client.post(
                "/api/v1/auth/holding-context", json={"filial_id": str(filial.id)}
            )
            assert result.status_code == 200, result.text
            delegated = result.json()
            me = (await client.get("/api/v1/auth/me")).json()
            assert me["user_id"] == str(owner.id)
            assert me["scope"] == "filial" and me["role_slug"] == "filial-admin"
            assert me["holding_context"] is True and me["filial_name"] == filial.name
            assert (
                await client.get("/api/v1/warehouses", params={"filial_id": str(filial.id)})
            ).status_code == 200
            for forbidden in [foreign.id, sibling.id]:
                assert (
                    await client.get("/api/v1/warehouses", params={"filial_id": str(forbidden)})
                ).status_code == 403
            response = await client.get("/api/v1/auth/access")
            assert (
                response.status_code == 200
                and response.json()["modules"]["usuarios-accesos"] == "editar"
            )
            response = await client.patch(
                f"/api/v1/users/{worker.id}",
                json={"permission_overrides": [{"module_id": "almacen", "access": "editar"}]},
            )
            assert response.status_code == 200, response.text
            response = await client.patch(f"/api/v1/users/{worker.id}", json={"status": "inactivo"})
            assert response.status_code == 200
            assert response.json()["status"] == "inactivo"
            response = await client.post("/api/v1/auth/refresh")
            assert response.status_code == 200
            assert decode_access_token(response.json()["access_token"])["filial_id"] == str(
                filial.id
            )
            assert (await client.get("/api/v1/auth/me")).json()["holding_context"] is True
            # A disabled subsidiary must stop access; returning to general view still works.
            filial.is_active = False
            await db.commit()
            assert (await client.get("/api/v1/auth/me")).status_code == 403
            response = await client.post("/api/v1/auth/holding-context", json={"filial_id": None})
            assert response.status_code == 200, response.text
            assert (await client.get("/api/v1/auth/me")).json()["scope"] == "holding"
            events = (
                (
                    await db.execute(
                        select(HoldingAuditEvent).where(
                            HoldingAuditEvent.user_id == owner.id,
                            HoldingAuditEvent.action == "context_switch",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 2
            assert events[0].user_id == owner.id
            # Ordinary filial users cannot use this to elevate their access.
            worker.status = UserStatus.ACTIVO
            filial.is_active = True
            await db.commit()
            worker_tokens = await AuthService(db)._token_response(worker)
            client.cookies.clear()
            client.headers["Authorization"] = f"Bearer {worker_tokens.access_token}"
            response = await client.post(
                "/api/v1/auth/holding-context", json={"filial_id": str(sibling.id)}
            )
            assert response.status_code == 403
