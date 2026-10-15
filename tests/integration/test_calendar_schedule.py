"""Calendar uses the workshop day; appointment and delivery are independent."""

from datetime import UTC, date, datetime

import pytest

from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import ServiceOrderUpdate
from app.modules.service_orders.service import ServiceOrderService


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(ServiceOrderStatus))
async def test_calendar_day_includes_all_statuses_and_caracas_midnight(billing_db, status):
    ctx = billing_db
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        order.status = status
        service = ServiceOrderService(db)
        for timestamp, expected_day in [
            (datetime(2026, 10, 16, 3, 59, tzinfo=UTC), date(2026, 10, 15)),
            (datetime(2026, 10, 16, 4, 0, tzinfo=UTC), date(2026, 10, 16)),
        ]:
            order.scheduled_at = timestamp
            await db.commit()
            matching = await service.list_orders(ctx.filial_id, scheduled_date=expected_day)
            assert order.id in {item.id for item in matching}
            other_day = date(2026, 10, 16) if expected_day.day == 15 else date(2026, 10, 15)
            other = await service.list_orders(ctx.filial_id, scheduled_date=other_day)
            assert order.id not in {item.id for item in other}


@pytest.mark.asyncio
async def test_edit_delivery_does_not_move_visit_and_can_clear_delivery(billing_db):
    ctx = billing_db
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        order.status = ServiceOrderStatus.PENDIENTE
        appointment = datetime(2026, 10, 15, 13, 30, tzinfo=UTC)
        order.scheduled_at = appointment
        order.promised_at = None
        await db.commit()
        service = ServiceOrderService(db)
        delivery = datetime(2026, 10, 16, 19, 0, tzinfo=UTC)
        updated = await service.update_order(order.id, ServiceOrderUpdate(promised_at=delivery))
        assert updated.scheduled_at == appointment
        assert updated.promised_at == delivery
        updated = await service.update_order(order.id, ServiceOrderUpdate(clear_promised_at=True))
        assert updated.promised_at is None
        assert updated.scheduled_at == appointment
