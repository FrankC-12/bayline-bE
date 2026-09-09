"""Reception data (mileage, customer reason, advisor, promised date) is
required to open a service order — the API rejects creation without it."""

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
from app.modules.service_orders.schemas import ServiceOrderCreate, ServiceOrderRead
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield ServiceOrderService(AsyncAdapter(session)), session


def _valid_payload(filial_id, vehicle_id, advisor_id):
    return ServiceOrderCreate(
        filial_id=filial_id,
        vehicle_id=vehicle_id,
        intake_mileage=15000,
        customer_reason="Ruido en frenos delanteros",
        advisor_user_id=advisor_id,
        promised_at=date(2026, 9, 10),
    )


def test_missing_reception_fields_are_rejected():
    with pytest.raises(ValidationError):
        ServiceOrderCreate(filial_id=uuid.uuid4(), vehicle_id=uuid.uuid4())


def test_empty_customer_reason_is_rejected():
    with pytest.raises(ValidationError):
        ServiceOrderCreate(
            filial_id=uuid.uuid4(),
            vehicle_id=uuid.uuid4(),
            intake_mileage=1000,
            customer_reason="",
            advisor_user_id=uuid.uuid4(),
            promised_at=date(2026, 9, 10),
        )


@pytest.mark.asyncio
async def test_create_order_persists_reception_data(env):
    service, _session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    order = await service.create_order(_valid_payload(filial_id, vehicle_id, advisor_id))

    assert order.intake_mileage == 15000
    assert order.customer_reason == "Ruido en frenos delanteros"
    assert order.advisor_user_id == advisor_id
    assert order.promised_at == date(2026, 9, 10)

    read = ServiceOrderRead.model_validate(order)
    assert read.intake_mileage == 15000
    assert read.promised_at == date(2026, 9, 10)
