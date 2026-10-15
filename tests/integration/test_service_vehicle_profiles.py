"""Service advisors can read vehicle profiles without client-administration access."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients import router as clients
from app.modules.clients.models import Vehicle
from app.modules.post_ventas import router as warranties
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders import router as orders
from app.modules.service_orders.models import ServiceOrder
from app.modules.users.models import User


@pytest.mark.asyncio
async def test_advisor_vehicle_profiles_are_filial_scoped_and_keep_clients_separate(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        role = Role(
            name="Service profiles QA", slug=f"qa-{uuid.uuid4().hex[:8]}", scope=RoleScope.FILIAL
        )
        db.add(role)
        await db.flush()
        db.add(
            RoleModulePermission(
                role_id=role.id, module_id="asesor-servicios", access=AccessLevel.EDITAR
            )
        )
        user = User(
            full_name="Advisor QA",
            email=f"{uuid.uuid4()}@example.com",
            role_id=role.id,
            filial_id=ctx.filial_id,
        )
        db.add(user)
        order = await db.get(ServiceOrder, ctx.order_id)
        vehicle = await db.get(Vehicle, order.vehicle_id)
        vehicle.vin = "1HGCM82633A004352"
        vehicle.mileage = 0
        await db.commit()
        current = CurrentUser(
            user_id=user.id,
            email=user.email,
            role_id=role.id,
            role_slug=role.slug,
            scope="filial",
            filial_id=ctx.filial_id,
            holding_id=None,
        )
        app = FastAPI()
        register_exception_handlers(app)
        for router in (clients.router, orders.router, warranties.router):
            app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: current
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            url = "/api/v1/service-orders/vehicles"
            response = await client.get(
                url, params={"filial_id": str(ctx.filial_id), "search": vehicle.vin, "page_size": 1}
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 1 and payload["items"][0]["vehicle"]["id"] == str(vehicle.id)
            assert payload["items"][0]["vehicle"]["mileage"] == 0
            assert set(payload["items"][0]) == {"vehicle", "client_name"}
            missing = await client.get(
                url, params={"filial_id": str(ctx.filial_id), "search": "unmatched-profile"}
            )
            assert missing.json()["total"] == 0
            foreign = await client.get(url, params={"filial_id": str(uuid.uuid4())})
            assert foreign.status_code == 403
            history = await client.get(f"/api/v1/clients/vehicles/{vehicle.id}/service-history")
            assert history.status_code == 200, history.text
            factory = await client.post(
                f"/api/v1/clients/vehicles/{vehicle.id}/factory-warranty",
                data={"starts_at": "2026-10-15", "duration_months": "36"},
            )
            assert factory.status_code == 201, factory.text
            warranty = await client.get(
                f"/api/v1/vehicle-warranties/by-vin/{vehicle.vin}",
                params={"filial_id": str(ctx.filial_id)},
            )
            assert warranty.status_code == 200, warranty.text
            workshop = await client.get(
                f"/api/v1/workshop-warranties/by-vin/{vehicle.vin}",
                params={"filial_id": str(ctx.filial_id)},
            )
            assert workshop.status_code == 200, workshop.text
            # The new service view does not grant access to client CRUD/listing.
            denied = await client.get("/api/v1/clients", params={"filial_id": str(ctx.filial_id)})
            assert denied.status_code == 403
