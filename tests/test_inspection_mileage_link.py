"""Kilometraje de ingreso on a ServiceOrder is always a view inherited from
its linked PreliminaryInspection. Linking one — whether at ODS creation or
later, once a vehicle scheduled ahead of time actually arrives — must mirror
the inspection's mileage onto the order."""

import os
import uuid
from datetime import date

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.inspections.exceptions import InspectionAlreadyLinkedError
from app.modules.inspections.models import PreliminaryInspection
from app.modules.inspections.schemas import InspectionUpdate
from app.modules.inspections.service import InspectionService
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import ServiceOrderCreate
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        adapter = AsyncAdapter(session)
        yield InspectionService(adapter), ServiceOrderService(adapter), session


async def _make_scheduled_order(service: ServiceOrderService, vehicle_id: uuid.UUID) -> ServiceOrder:
    return await service.create_order(
        ServiceOrderCreate(
            filial_id=uuid.uuid4(),
            vehicle_id=vehicle_id,
            customer_reason="Mantenimiento programado",
            advisor_user_id=uuid.uuid4(),
            promised_at=date(2026, 9, 10),
            scheduled_at="2026-09-15T10:00:00+00:00",
        )
    )


@pytest.mark.asyncio
async def test_linking_an_inspection_mirrors_its_mileage_onto_the_order(env):
    inspections, orders, session = env
    vehicle_id = uuid.uuid4()
    order = await _make_scheduled_order(orders, vehicle_id)
    assert order.intake_mileage is None

    inspection = PreliminaryInspection(
        filial_id=order.filial_id,
        vehicle_id=vehicle_id,
        inspector_user_id=uuid.uuid4(),
        mileage=18500,
    )
    session.add(inspection)
    session.flush()

    await inspections.update_inspection(inspection.id, InspectionUpdate(service_order_id=order.id))

    refreshed = session.get(ServiceOrder, order.id)
    assert refreshed.intake_mileage == 18500


@pytest.mark.asyncio
async def test_cannot_link_an_already_linked_inspection(env):
    inspections, orders, session = env
    vehicle_id = uuid.uuid4()
    order_a = await _make_scheduled_order(orders, vehicle_id)
    order_b = await _make_scheduled_order(orders, vehicle_id)

    inspection = PreliminaryInspection(
        filial_id=order_a.filial_id,
        vehicle_id=vehicle_id,
        inspector_user_id=uuid.uuid4(),
        mileage=18500,
        service_order_id=order_a.id,
    )
    session.add(inspection)
    session.flush()

    with pytest.raises(InspectionAlreadyLinkedError):
        await inspections.update_inspection(inspection.id, InspectionUpdate(service_order_id=order_b.id))
