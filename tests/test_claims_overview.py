"""Dashboard aggregates keep direct costs and tenant/date boundaries intact."""

import uuid
from datetime import UTC, date, datetime

import pytest
import test_pending_upsells_cross_visit as visit_fixtures
from sqlalchemy import event

from app.modules.service_orders import claims_workflow
from app.modules.service_orders.claims_workflow import ClaimsWorkflow
from app.modules.service_orders.enums import (
    ServiceOrderStatus,
    TaskStatus,
    WarrantyClaimStatus,
    WarrantyClaimType,
)
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    WarrantyClaim,
)

visit_env = visit_fixtures.env


def rework(session, origin, catalog, *, sequence, converted_at, rate=25, filial_id=None):
    order = ServiceOrder(
        filial_id=filial_id or origin.filial_id,
        vehicle_id=origin.vehicle_id,
        order_type_id=origin.order_type_id,
        sequence_number=sequence,
        settings_snapshot={"hourly_rate": rate},
    )
    session.add(order)
    session.flush()
    claim = WarrantyClaim(
        filial_id=filial_id or origin.filial_id,
        sequence_number=sequence,
        claim_type=WarrantyClaimType.COMEBACK,
        vehicle_id=origin.vehicle_id,
        service_order_id=origin.id,
        resulting_service_order_id=order.id,
        reported_mileage=0,
        claimed_at=date(2026, 10, 19),
        converted_at=converted_at,
        status=WarrantyClaimStatus.CONVERTIDO_A_ODS,
    )
    session.add(claim)
    return order


def task(session, order, catalog, hours, status):
    session.add(
        ServiceOrderTask(
            service_order_id=order.id,
            tempario_id=catalog.id,
            code_snapshot=catalog.code,
            name_snapshot=catalog.name,
            hours_snapshot=hours,
            status=status,
        )
    )


def parts(session, order, costs, *, dispatched):
    transfer = ServiceOrderTransfer(
        service_order_id=order.id, sequence_number=1 if dispatched else 2, stock_deducted=dispatched
    )
    session.add(transfer)
    session.flush()
    for cost in costs:
        session.add(
            ServiceOrderTransferLine(
                transfer_id=transfer.id,
                part_id=uuid.uuid4(),
                quantity=1,
                cost_total=cost,
                unit_price=999,
                line_total=999,
            )
        )


@pytest.mark.asyncio
async def test_cost_aggregates_do_not_multiply_parts_by_tasks_and_use_frozen_rates(
    visit_env, monkeypatch
):
    service, session, settings, origin, _, _, catalog = visit_env
    monkeypatch.setattr(claims_workflow, "venezuela_today", lambda: date(2026, 10, 19))
    first = rework(
        session,
        origin,
        catalog,
        sequence=10,
        converted_at=datetime(2026, 10, 2, 12, tzinfo=UTC),
        rate=25,
    )
    task(session, first, catalog, 1.5, TaskStatus.COMPLETADA)
    task(session, first, catalog, 2, TaskStatus.COMPLETADA)
    task(session, first, catalog, 10, TaskStatus.PENDIENTE)
    parts(session, first, [10.25, 20.10], dispatched=True)
    parts(session, first, [900], dispatched=False)
    second = rework(
        session,
        origin,
        catalog,
        sequence=11,
        converted_at=datetime(2026, 10, 3, 12, tzinfo=UTC),
        rate=40,
    )
    task(session, second, catalog, 0.5, TaskStatus.COMPLETADA)
    parts(session, second, [5, 7], dispatched=True)
    settings.hourly_rate = 999
    origin.status = ServiceOrderStatus.ORDEN_CERRADA
    origin.closed_at = datetime(2026, 10, 4, 12, tzinfo=UTC)
    session.commit()
    result = await ClaimsWorkflow(service.db).overview(origin.filial_id)
    assert result == {
        "pending": 0,
        "monthly_cost": pytest.approx(149.85),
        "monthly_reworks": 2,
        "monthly_closed": 1,
        "comeback_rate": 200.0,
    }


@pytest.mark.asyncio
async def test_month_boundary_is_caracas_and_other_filials_cannot_affect_totals(
    visit_env, monkeypatch
):
    service, session, _, origin, _, _, catalog = visit_env
    monkeypatch.setattr(claims_workflow, "venezuela_today", lambda: date(2026, 10, 19))
    before = rework(
        session,
        origin,
        catalog,
        sequence=10,
        converted_at=datetime(2026, 10, 1, 3, 59, 59, tzinfo=UTC),
    )
    parts(session, before, [100], dispatched=True)
    included = rework(
        session, origin, catalog, sequence=11, converted_at=datetime(2026, 10, 1, 4, tzinfo=UTC)
    )
    parts(session, included, [12.34], dispatched=True)
    other = rework(
        session,
        origin,
        catalog,
        sequence=12,
        converted_at=datetime(2026, 10, 1, 4, tzinfo=UTC),
        filial_id=uuid.uuid4(),
    )
    parts(session, other, [1000], dispatched=True)
    origin.status = ServiceOrderStatus.ORDEN_CERRADA
    origin.closed_at = datetime(2026, 10, 1, 3, 59, 59, tzinfo=UTC)
    session.commit()
    result = await ClaimsWorkflow(service.db).overview(origin.filial_id)
    assert result["monthly_cost"] == 12.34
    assert result["monthly_reworks"] == 1
    assert result["monthly_closed"] == 0
    assert result["comeback_rate"] == 0


@pytest.mark.asyncio
async def test_query_count_is_bounded_with_many_reworks(visit_env, monkeypatch):
    service, session, _, origin, _, _, catalog = visit_env
    monkeypatch.setattr(claims_workflow, "venezuela_today", lambda: date(2026, 10, 19))
    for sequence in range(10, 40):
        rework(
            session,
            origin,
            catalog,
            sequence=sequence,
            converted_at=datetime(2026, 10, 2, 12, tzinfo=UTC),
        )
    session.commit()
    queries = []

    def capture(_connection, _cursor, statement, *_args):
        queries.append(statement)

    event.listen(session.get_bind(), "before_cursor_execute", capture)
    try:
        result = await ClaimsWorkflow(service.db).overview(origin.filial_id)
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", capture)
    assert result["monthly_reworks"] == 30
    assert result["monthly_cost"] == 0
    assert len(queries) <= 5


@pytest.mark.asyncio
async def test_empty_filial_returns_zero_counts_without_cost_queries(visit_env, monkeypatch):
    service, session, _, _, _, _, _ = visit_env
    monkeypatch.setattr(claims_workflow, "venezuela_today", lambda: date(2026, 10, 19))
    result = await ClaimsWorkflow(service.db).overview(uuid.uuid4())
    assert result == {
        "pending": 0,
        "monthly_cost": 0,
        "monthly_reworks": 0,
        "monthly_closed": 0,
        "comeback_rate": 0,
    }


@pytest.mark.asyncio
async def test_pending_count_includes_older_requests_but_only_from_current_filial(visit_env):
    service, session, _, origin, _, _, _ = visit_env
    for sequence, filial_id in [(1, origin.filial_id), (2, uuid.uuid4())]:
        session.add(
            WarrantyClaim(
                filial_id=filial_id,
                sequence_number=sequence,
                claim_type=WarrantyClaimType.COMEBACK,
                vehicle_id=origin.vehicle_id,
                service_order_id=origin.id,
                reported_mileage=0,
                claimed_at=date(2020, 1, 1),
                status=WarrantyClaimStatus.SOLICITADO,
            )
        )
    session.commit()
    result = await ClaimsWorkflow(service.db).overview(origin.filial_id)
    assert result["pending"] == 1
    assert result["monthly_reworks"] == 0
