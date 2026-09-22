"""Reception data (customer reason, advisor, promised date) is required to
open a service order — the API rejects creation without it. intake_mileage
is never entered by hand: it's always a read-only view inherited from a
PreliminaryInspection. A walk-in ODS (no scheduled_at — the vehicle is
physically present) must reference an existing unlinked inspection for the
vehicle; one scheduled ahead of time via "Agendar Orden de Servicio" can't
know it yet, so it may omit inspection_id and get it linked later, once the
vehicle arrives, via InspectionService.update_inspection."""

import os
import uuid
from datetime import date

os.environ["DEBUG"] = "false"

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.inspections.models import PreliminaryInspection
from app.modules.service_orders.schemas import ServiceOrderCreate, ServiceOrderRead
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield ServiceOrderService(AsyncAdapter(session)), session


def _make_inspection(session, vehicle_id, mileage=15000, service_order_id=None):
    inspection = PreliminaryInspection(
        filial_id=uuid.uuid4(),
        vehicle_id=vehicle_id,
        inspector_user_id=uuid.uuid4(),
        mileage=mileage,
        service_order_id=service_order_id,
    )
    session.add(inspection)
    session.flush()
    return inspection


def _payload(filial_id, vehicle_id, advisor_id, **overrides):
    fields = {
        "filial_id": filial_id,
        "vehicle_id": vehicle_id,
        "customer_reason": "Ruido en frenos delanteros",
        "advisor_user_id": advisor_id,
        "promised_at": date(2026, 9, 10),
    }
    fields.update(overrides)
    return ServiceOrderCreate(**fields)


def test_missing_reception_fields_are_rejected():
    with pytest.raises(ValidationError):
        ServiceOrderCreate(filial_id=uuid.uuid4(), vehicle_id=uuid.uuid4())


def test_empty_customer_reason_is_rejected():
    with pytest.raises(ValidationError):
        ServiceOrderCreate(
            filial_id=uuid.uuid4(),
            vehicle_id=uuid.uuid4(),
            customer_reason="",
            advisor_user_id=uuid.uuid4(),
            promised_at=date(2026, 9, 10),
        )


@pytest.mark.asyncio
async def test_walk_in_order_requires_an_inspection(env):
    service, _session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    with pytest.raises(BadRequestError):
        await service.create_order(_payload(filial_id, vehicle_id, advisor_id))


@pytest.mark.asyncio
async def test_create_order_inherits_mileage_from_inspection_and_links_it(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id, mileage=15000)

    order = await service.create_order(
        _payload(filial_id, vehicle_id, advisor_id, inspection_id=inspection.id)
    )

    assert order.intake_mileage == 15000
    assert order.customer_reason == "Ruido en frenos delanteros"
    assert order.advisor_user_id == advisor_id
    assert order.promised_at == date(2026, 9, 10)
    assert inspection.service_order_id == order.id

    read = ServiceOrderRead.model_validate(order)
    assert read.intake_mileage == 15000
    assert read.promised_at == date(2026, 9, 10)


@pytest.mark.asyncio
async def test_inspection_for_a_different_vehicle_is_rejected(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other_vehicle_inspection = _make_inspection(session, uuid.uuid4(), mileage=15000)

    with pytest.raises(BadRequestError):
        await service.create_order(
            _payload(filial_id, vehicle_id, advisor_id, inspection_id=other_vehicle_inspection.id)
        )


@pytest.mark.asyncio
async def test_already_linked_inspection_is_rejected(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id, mileage=15000, service_order_id=uuid.uuid4())

    with pytest.raises(BadRequestError):
        await service.create_order(
            _payload(filial_id, vehicle_id, advisor_id, inspection_id=inspection.id)
        )


@pytest.mark.asyncio
async def test_create_order_inherits_customer_reason_from_inspection_notes(env):
    """customer_reason is heredado from the inspection's notes just like
    intake_mileage — whatever the client sends in customer_reason is
    ignored once the inspection has its own notes recorded."""
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id, mileage=15000)
    inspection.notes = "Ruido metálico en suspensión delantera"
    session.flush()

    order = await service.create_order(
        _payload(
            filial_id, vehicle_id, advisor_id,
            inspection_id=inspection.id, customer_reason="Motivo distinto tecleado en el panel",
        )
    )

    assert order.customer_reason == "Ruido metálico en suspensión delantera"


@pytest.mark.asyncio
async def test_create_order_requires_a_reason_when_neither_side_has_one(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id, mileage=15000)

    with pytest.raises(BadRequestError):
        await service.create_order(
            _payload(filial_id, vehicle_id, advisor_id, inspection_id=inspection.id, customer_reason=None)
        )


@pytest.mark.asyncio
async def test_scheduled_order_can_omit_inspection_and_mileage(env):
    service, _session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    order = await service.create_order(
        _payload(filial_id, vehicle_id, advisor_id, scheduled_at="2026-09-15T10:00:00+00:00")
    )

    assert order.intake_mileage is None
