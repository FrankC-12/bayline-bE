"""Workshop overview counts and Caracas day boundaries, isolated by filial."""

from datetime import date, datetime, timedelta, timezone
import uuid

import pytest
from test_kpis_rework_report import env, make_vehicle
from test_part_sales_fifo import get_or_make_order_type
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder


@pytest.mark.asyncio
async def test_overview_empty(env, monkeypatch):
    service, _, filial_id, _ = env
    monkeypatch.setattr("app.modules.kpis.service.venezuela_today", lambda: date(2026, 10, 28))
    report = await service.get_overview(filial_id)
    assert report.active_orders == report.registered_vehicles == report.maintenance_overdue == 0
    assert len(report.trend) == 7
    assert report.trend[0].day == date(2026, 10, 22)
    assert all(day.created == day.delivered == 0 for day in report.trend)


@pytest.mark.asyncio
async def test_overview_statuses_maintenance_tenant_and_day_boundaries(env, monkeypatch):
    service, session, filial_id, client_id = env
    today = date(2026, 10, 28)
    monkeypatch.setattr("app.modules.kpis.service.venezuela_today", lambda: today)
    for offset in [-1, 0, 7, 8, 30, 31]:
        vehicle = make_vehicle(session, client_id)
        vehicle.next_maintenance_due_at = today + timedelta(days=offset)
    session.commit()
    order_type = get_or_make_order_type(session, filial_id)

    def order(status, created, draft=False, closed=None, filial=filial_id):
        session.add(
            ServiceOrder(
                filial_id=filial,
                sequence_number=uuid.uuid4().int % 1000000,
                order_type_id=order_type,
                status=status,
                is_draft=draft,
                created_at=created,
                closed_at=closed,
            )
        )

    # 03:59 UTC is the previous Venezuelan day; 04:00 is the new day.
    before = datetime(2026, 10, 28, 3, 59, tzinfo=timezone.utc)
    midnight = datetime(2026, 10, 28, 4, tzinfo=timezone.utc)
    order(ServiceOrderStatus.PENDIENTE, before)
    order(ServiceOrderStatus.EN_PROGRESO, midnight)
    order(ServiceOrderStatus.COMPLETADO, midnight)
    order(ServiceOrderStatus.ORDEN_CERRADA, before, closed=midnight)
    order(ServiceOrderStatus.CANCELADO, midnight)
    order(ServiceOrderStatus.PENDIENTE, midnight, draft=True)
    order(ServiceOrderStatus.EN_PROGRESO, midnight, filial=uuid.uuid4())
    order(ServiceOrderStatus.CANCELADO, datetime(2026, 10, 29, 4, tzinfo=timezone.utc))
    session.commit()
    report = await service.get_overview(filial_id)
    assert report.active_orders == 3
    assert (
        report.pending_orders,
        report.in_progress_orders,
        report.ready_orders,
        report.draft_orders,
    ) == (1, 1, 1, 1)
    assert report.registered_vehicles == 6
    assert (report.maintenance_overdue, report.maintenance_due_7, report.maintenance_due_30) == (
        1,
        2,
        4,
    )
    assert report.trend[-2].created == 2
    assert report.trend[-1].created == 3
    assert report.trend[-1].delivered == 1
    other = await service.get_overview(uuid.uuid4())
    assert other.active_orders == other.registered_vehicles == 0
