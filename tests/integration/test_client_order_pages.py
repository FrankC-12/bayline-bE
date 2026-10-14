import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.router import router as client_router
from app.modules.clients.service import ClientService
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.router import router as order_router
from app.modules.service_orders.service import ServiceOrderService


@pytest.mark.asyncio
async def test_client_pages_preserve_accent_plate_vin_search_and_updates(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        clients = [
            Client(
                filial_id=ctx.filial_id,
                full_name=name,
                client_type=ClientType.PARTICULAR,
                document_type=DocumentType.V,
                document_number=str(55555550 + index),
                phone_primary="04121234567",
                address="Caracas",
            )
            for index, name in enumerate(("José Ramírez", "Andrés", "François", "100% Cliente"))
        ]
        db.add_all(clients)
        await db.flush()
        db.add(
            Vehicle(
                client_id=clients[0].id,
                brand="Toyota",
                model="Hilux",
                plate="AB456CD",
                vin="ABCDEFG1234567890",
            )
        )
        await db.commit()
        service = ClientService(db)
        first = await service.page_clients(ctx.filial_id, page_size=2)
        second = await service.page_clients(ctx.filial_id, page_size=2, page=2)
        assert first.total == 5
        assert len(first.items) == len(second.items) == 2
        assert not {client.id for client in first.items} & {client.id for client in second.items}
        for search in ("jose", "JOSÉ", "ramirez", "AB456", "abcdefg", "55555550"):
            result = await service.page_clients(ctx.filial_id, search=search)
            assert result.total == 1
            assert result.items[0].id == clients[0].id
        assert (await service.page_clients(ctx.filial_id, search="100%")).total == 1
        assert (await service.page_clients(uuid.uuid4())).total == 0
        clients[0].full_name = "María Fernández"
        await db.commit()
        assert (await service.page_clients(ctx.filial_id, search="maria fernandez")).total == 1
        assert (await service.page_clients(ctx.filial_id, search="jose")).total == 0


@pytest.mark.asyncio
async def test_order_pages_return_labels_apply_filters_and_technician_scope_before_count(
    billing_db,
):
    ctx = billing_db
    async with ctx.sessions() as db:
        original = await db.get(ServiceOrder, ctx.order_id)
        client = (
            await db.execute(select(Client).where(Client.filial_id == ctx.filial_id))
        ).scalar_one()
        client.full_name = "José Mecánica"
        original.status = ServiceOrderStatus.ORDEN_CERRADA
        original.created_at = datetime(2026, 10, 14, 3, tzinfo=UTC)
        db.add(
            ServiceOrder(
                filial_id=ctx.filial_id,
                vehicle_id=original.vehicle_id,
                order_type_id=original.order_type_id,
                sequence_number=2,
                status=ServiceOrderStatus.CANCELADO,
                created_at=datetime(2026, 10, 14, 4, tzinfo=UTC),
            )
        )
        await db.commit()
        service = ServiceOrderService(db)
        first = await service.page_orders(ctx.filial_id, view="history", page_size=1)
        second = await service.page_orders(ctx.filial_id, view="history", page_size=1, page=2)
        assert first.total == second.total == 2
        assert first.items[0].id != second.items[0].id
        assert first.items[0].client_name == "José Mecánica"
        assert first.items[0].vehicle_label == "Toyota Hilux"
        assert (await service.page_orders(ctx.filial_id, view="history", search="jose")).total == 2
        assert (await service.page_orders(ctx.filial_id, view="history", search="ODS-2")).total == 1
        assert (await service.page_orders(ctx.filial_id, view="active")).total == 0
        assert (
            await service.page_orders(
                ctx.filial_id, view="history", status=ServiceOrderStatus.CANCELADO
            )
        ).total == 1
        assert (
            await service.page_orders(
                ctx.filial_id,
                view="history",
                date_from=date(2026, 10, 13),
                date_to=date(2026, 10, 13),
            )
        ).total == 1
        assert (
            await service.page_orders(
                ctx.filial_id, view="history", technician_user_id=uuid.uuid4()
            )
        ).total == 0


@pytest.mark.asyncio
async def test_client_and_order_page_endpoints_check_permissions_and_limits(billing_db):
    ctx = billing_db
    app = FastAPI()
    app.include_router(client_router, prefix="/api/v1")
    app.include_router(order_router, prefix="/api/v1")
    register_exception_handlers(app)
    user = CurrentUser(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role_id=uuid.uuid4(),
        role_slug="filial-admin",
        scope="filial",
        filial_id=ctx.filial_id,
        holding_id=None,
    )
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for root in ("clients", "service-orders"):
            path = f"/api/v1/{root}/page?filial_id={ctx.filial_id}"
            result = await http.get(path)
            assert result.status_code == 200
            assert result.json()["total"] == 1
            for invalid in ("page=0", "page_size=0", "page_size=101"):
                assert (await http.get(path + "&" + invalid)).status_code == 422
            assert (
                await http.get(f"/api/v1/{root}/page?filial_id={uuid.uuid4()}")
            ).status_code == 403
        assert (
            await http.get(
                f"/api/v1/service-orders/page?filial_id={ctx.filial_id}&date_from=2026-10-14&date_to=2026-10-13"
            )
        ).status_code == 400
        # Both legacy list contracts stay available to selectors and mobile.
        assert isinstance(
            (await http.get(f"/api/v1/clients?filial_id={ctx.filial_id}")).json(), list
        )
        assert isinstance(
            (await http.get(f"/api/v1/service-orders?filial_id={ctx.filial_id}")).json(), list
        )


@pytest.mark.asyncio
async def test_order_page_http_counts_only_orders_assigned_to_real_technician(billing_db):
    from test_detailed_inspections import mobile

    ctx = billing_db
    app = await mobile(ctx)
    app.include_router(order_router, prefix="/api/v1")
    async with ctx.sessions() as db:
        original = await db.get(ServiceOrder, ctx.order_id)
        db.add(
            ServiceOrder(
                filial_id=ctx.filial_id,
                vehicle_id=original.vehicle_id,
                order_type_id=original.order_type_id,
                sequence_number=2,
                status=ServiceOrderStatus.COMPLETADO,
            )
        )
        await db.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get(
            f"/api/v1/service-orders/page?filial_id={ctx.filial_id}&view=all&page_size=1"
        )
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == 1
        assert result["total_pages"] == 1
        assert result["items"][0]["id"] == str(ctx.order_id)
