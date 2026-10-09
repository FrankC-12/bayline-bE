"""The workshop-to-warehouse feed must work with fresh asynchronous sessions."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part, PartCategory
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders import router as order_routes
from app.modules.service_orders.service import ServiceOrderService
from app.modules.users.models import User
from app.modules.warehouse import router as routes
from app.modules.warehouse.models import PartLot, Warehouse
from app.modules.warehouse.service import AlmacenService


@pytest.mark.asyncio
async def test_dispatched_workshop_transfer_is_visible_in_warehouse_http_feed(
    billing_db, monkeypatch
):
    ctx = billing_db
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        category = PartCategory(holding_id=filial.holding_id, name="Lubricantes")
        warehouse = Warehouse(filial_id=ctx.filial_id, name="Principal")
        db.add_all([category, warehouse])
        await db.flush()
        part = Part(
            filial_id=ctx.filial_id,
            category_id=category.id,
            code="QA-002",
            name="Aceite 15W40",
            price=13,
        )
        db.add(part)
        await db.flush()
        db.add(
            PartLot(
                filial_id=ctx.filial_id,
                warehouse_id=warehouse.id,
                part_id=part.id,
                quantity_received=5,
                quantity_remaining=5,
                unit_cost=10,
            )
        )
        await db.commit()
        workshop = ServiceOrderService(db)
        await workshop.add_transfer_line(ctx.order_id, part.id, 2)
        transfer = (await workshop.list_transfers(ctx.order_id))[0]
        # Drafts should not arrive at Almacén before the workshop submits the request.
        async with ctx.sessions() as reader:
            assert await AlmacenService(reader).list_service_order_requests(ctx.filial_id) == []
        await workshop.mark_transfer_ordered(transfer.id)
        transfer_id = transfer.id

    # A new HTTP request/session prevents SQLite identity-map caching from hiding lazy loads.
    async with ctx.sessions() as db:
        service = AlmacenService(db)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(routes.router, prefix="/api/v1")
        app.include_router(order_routes.router, prefix="/api/v1")
        role = Role(
            name="Almacenista QA", slug=f"almacenista-qa-{uuid.uuid4()}", scope=RoleScope.FILIAL
        )
        db.add(role)
        await db.flush()
        db.add(
            RoleModulePermission(role_id=role.id, module_id="almacen", access=AccessLevel.EDITAR)
        )
        operator = User(
            full_name="Almacenista QA",
            email=f"{uuid.uuid4()}@example.com",
            role_id=role.id,
            filial_id=ctx.filial_id,
        )
        db.add(operator)
        await db.commit()
        user = CurrentUser(
            user_id=operator.id,
            email=operator.email,
            role_id=role.id,
            role_slug=role.slug,
            scope="filial",
            filial_id=ctx.filial_id,
            holding_id=None,
        )
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[routes.get_service] = lambda: service
        app.dependency_overrides[order_routes.get_service] = lambda: ServiceOrderService(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            path = f"/api/v1/almacen/service-order-requests?filial_id={ctx.filial_id}"
            response = await http.get(path)
            assert response.status_code == 200
            row = response.json()[0]
            assert row["id"] == str(transfer_id)
            assert row["status"] == "pedido"
            assert row["stage"] == "pendiente"
            assert row["warehouse_seen"] is False
            assert row["lines"][0]["part_name"] == "Aceite 15W40"
            assert row["lines"][0]["warehouses"][0]["warehouse_name"] == "Principal"
            assert row["lines"][0]["warehouses"][0]["quantity"] == 2
            # The warehouse feed must provide enough detail without granting
            # access to the advisor's ODS screen.
            assert (await http.get(f"/api/v1/service-orders/{ctx.order_id}")).status_code == 403
            assert (
                await http.post(
                    f"/api/v1/almacen/service-order-requests/{transfer_id}/acknowledge?filial_id={ctx.filial_id}"
                )
            ).status_code == 204
            assert (
                await http.post(
                    f"/api/v1/almacen/service-order-requests/{transfer_id}/start?filial_id={ctx.filial_id}"
                )
            ).status_code == 204
            assert (await http.get(path)).json()[0]["stage"] == "en_progreso"
            assert (
                await http.post(
                    f"/api/v1/almacen/service-order-requests/{transfer_id}/complete?filial_id={ctx.filial_id}"
                )
            ).status_code == 204
            response = await http.get(path)
            assert response.json()[0]["status"] == "completado"
            assert response.json()[0]["stage"] == "por_retirar"
            assert (
                await http.get(f"/api/v1/almacen/service-order-requests?filial_id={uuid.uuid4()}")
            ).status_code == 403

            pickup_path = f"/api/v1/service-order-transfers/{transfer_id}/pickup"
            photo = {"photo": ("retiro.jpg", b"photo", "image/jpeg")}
            assert (await http.post(pickup_path, files=photo)).status_code == 403
            db.add(
                RoleModulePermission(
                    role_id=role.id, module_id="asesor-servicios", access=AccessLevel.EDITAR
                )
            )
            await db.commit()
            assert (await http.post(pickup_path)).status_code == 422
            stored = []

            async def save_photo(file, **kwargs):
                stored.append(file.filename)
                return "/api/v1/uploads/dispatch-pickups/retirada.jpg"

            monkeypatch.setattr(order_routes, "save_upload_image", save_photo)
            assert (await http.post(pickup_path, files=photo)).status_code == 204
            row = (await http.get(path)).json()[0]
            assert row["stage"] == "completado"
            assert row["picked_up_at"] is not None
            assert row["pickup_photo_url"].endswith("retirada.jpg")
            assert (await http.post(pickup_path, files=photo)).status_code == 204
            assert stored == ["retiro.jpg"]
