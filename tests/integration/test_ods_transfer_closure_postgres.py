"""The delivery API closes ODTs atomically and releases warehouse reservations."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.service_orders.billing import BillingService
from app.modules.service_orders.dispatch import WorkshopDispatchService
from app.modules.service_orders.models import ServiceOrderTransfer
from app.modules.service_orders.router import router
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.enums import TransferStatus
from app.modules.warehouse.models import PartLot, StockMovement, Transfer
from app.modules.warehouse.reservations import reserved_by_lot
from app.modules.warehouse.service import AlmacenService
from tests.integration.conftest import invoice_input
from tests.integration.test_mobile_workshop import technician
from tests.integration.test_workshop_dispatch_postgres import inventory


@pytest.mark.asyncio
async def test_delivery_api_closes_requests_and_pending_relocations_without_moving_stock(
    billing_db,
):
    ctx = billing_db
    actor = await technician(ctx)
    advisor = actor.model_copy(update={"role_slug": "filial-admin"})
    part_id, _, _ = await inventory(ctx, local=1, remote=3)
    async with ctx.sessions() as db:
        service = ServiceOrderService(db)
        request = await service.add_transfer_line(ctx.order_id, part_id, 2)
        key = request.id
        await WorkshopDispatchService(db).submit(key, actor.user_id)
        assert await reserved_by_lot(db, part_id)
        await service.force_complete_order(ctx.order_id, actor)
    payload = await invoice_input(ctx)
    async with ctx.sessions() as db:
        await BillingService(db).issue(ctx.order_id, payload, actor.user_id)
        before = (
            await db.execute(
                select(PartLot.id, PartLot.quantity_remaining)
                .where(PartLot.part_id == part_id)
                .order_by(PartLot.id)
            )
        ).all()
        movements = (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.part_id == part_id)
            )
        ).scalar_one()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: advisor

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post(f"/api/v1/service-orders/{ctx.order_id}/close")
        assert response.status_code == 200, response.text
        closed_at = response.json()["closed_at"]
        summary = await http.get(f"/api/v1/service-orders/{ctx.order_id}/summary")
        assert summary.status_code == 200, summary.text
        assert summary.json()["transfers"][0]["closed_at"] == closed_at
        assert summary.json()["transfers"][0]["closed_by_user_id"] == str(actor.user_id)
        repeated = await http.post(f"/api/v1/service-orders/{ctx.order_id}/close")
        assert repeated.status_code == 409
    async with ctx.sessions() as db:
        request = await db.get(ServiceOrderTransfer, key)
        assert request.closed_at is not None and request.closed_by_user_id == actor.user_id
        assert request.stock_deducted is False and request.picked_up_at is None
        assert await reserved_by_lot(db, part_id) == {}
        remaining = (
            await db.execute(
                select(PartLot.id, PartLot.quantity_remaining)
                .where(PartLot.part_id == part_id)
                .order_by(PartLot.id)
            )
        ).all()
        assert remaining == before
        assert (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.part_id == part_id)
            )
        ).scalar_one() == movements
        linked = (
            (await db.execute(select(Transfer).where(Transfer.workshop_request_id == key)))
            .scalars()
            .all()
        )
        assert linked and all(t.status == TransferStatus.CANCELADA for t in linked)
        feed = await AlmacenService(db).list_service_order_requests(ctx.filial_id)
        row = next(r for r in feed if r.id == key)
        assert row.stage == "completado" and row.closed_at
        from app.core.exceptions import ConflictError

        with pytest.raises(ConflictError):
            await WorkshopDispatchService(db).dispatch(key, actor.user_id)
