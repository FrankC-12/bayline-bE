"""PostgreSQL overview uses Venezuela dates for the daily chart."""

from datetime import date, datetime, timezone

import pytest

from app.modules.kpis.service import KpiService
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder


@pytest.mark.asyncio
async def test_workshop_overview_caracas_postgres(billing_db, monkeypatch):
    ctx = billing_db
    monkeypatch.setattr("app.modules.kpis.service.venezuela_today", lambda: date(2026, 10, 28))
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        order.created_at = datetime(2026, 10, 28, 3, 59, tzinfo=timezone.utc)
        order.closed_at = datetime(2026, 10, 28, 4, 0, tzinfo=timezone.utc)
        order.status = ServiceOrderStatus.ORDEN_CERRADA
        await db.commit()
        overview = await KpiService(db).get_overview(ctx.filial_id)
        assert overview.registered_vehicles == 1
        assert overview.active_orders == 0
        assert overview.trend[-2].created == 1
        assert overview.trend[-1].created == 0
        assert overview.trend[-1].delivered == 1
