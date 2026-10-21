"""Current-mileage lookups should not hydrate a vehicle's complete history."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from test_client_current_mileage import _inspection, env
from test_part_sales_fifo import make_order_type

from app.modules.inspections.models import PreliminaryInspection
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder

__all__ = ["env"]


@pytest.mark.asyncio
async def test_latest_lookup_loads_one_inspection_instead_of_entire_history(env):
    service, session, _, _, vehicle = env
    vehicle_id = vehicle.id
    start = datetime(2026, 1, 1, tzinfo=UTC)
    session.add_all(
        _inspection(vehicle_id, 10000 + i, start + timedelta(days=i)) for i in range(200)
    )
    session.commit()
    session.expunge_all()
    loaded = []

    def track(_session, instance):
        if isinstance(instance, PreliminaryInspection):
            loaded.append(instance)

    event.listen(session, "loaded_as_persistent", track)
    try:
        latest = await service._latest_mileage_inspections([vehicle_id])
    finally:
        event.remove(session, "loaded_as_persistent", track)
    assert latest[vehicle_id].mileage == 10199
    assert len(loaded) == 1


@pytest.mark.asyncio
async def test_equal_timestamps_have_a_deterministic_inspection_winner(env):
    service, session, _, _, vehicle = env
    first = _inspection(vehicle.id, 100, datetime(2026, 1, 1, tzinfo=UTC))
    second = _inspection(vehicle.id, 200, first.created_at)
    first.id, second.id = uuid.UUID(int=1), uuid.UUID(int=2)
    session.add_all([second, first])
    session.commit()
    latest = await service._latest_mileage_inspections([vehicle.id])
    assert latest[vehicle.id].id == first.id


@pytest.mark.asyncio
async def test_last_dated_delivery_wins_over_older_higher_mileage(env):
    service, session, filial_id, _, vehicle = env
    kind = make_order_type(session, filial_id)
    session.add_all(
        [
            ServiceOrder(
                filial_id=filial_id,
                vehicle_id=vehicle.id,
                sequence_number=i + 1,
                order_type_id=kind,
                status=ServiceOrderStatus.ORDEN_CERRADA,
                exit_mileage=mileage,
                closed_at=closed_at,
            )
            for i, (mileage, closed_at) in enumerate(
                [
                    (50000, datetime(2026, 1, 1, tzinfo=UTC)),
                    (40000, datetime(2026, 2, 1, tzinfo=UTC)),
                    (90000, None),
                ]
            )
        ]
    )
    session.commit()

    await service.attach_vehicle_mileage([vehicle])

    assert vehicle.current_mileage == 40000
    assert vehicle.current_mileage_service_order_code == "ODS-2"
    assert vehicle.mileage == 0


@pytest.mark.asyncio
async def test_empty_vehicle_list_does_not_query_database(env, monkeypatch):
    service, *_ = env

    async def unexpected_query(*_args, **_kwargs):
        raise AssertionError("An empty vehicle list must not query mileage history")

    monkeypatch.setattr(service.db, "execute", unexpected_query)
    await service.attach_vehicle_mileage([])
