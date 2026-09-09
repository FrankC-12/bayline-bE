"""Regression tests for exposing a vehicle's original vs. latest-visit
mileage, sourced from PreliminaryInspection, without touching the
registration Vehicle.mileage field."""

import os
import uuid
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.service import ClientService
from app.modules.inspections.models import PreliminaryInspection
from app.modules.service_orders.models import ServiceOrder


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        client = Client(
            id=uuid.uuid4(),
            filial_id=filial_id,
            full_name="Cliente Prueba",
            client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V,
            document_number="12345678",
            phone_primary="04121234567",
            address="Caracas",
        )
        vehicle = Vehicle(
            id=uuid.uuid4(), client_id=client.id, brand="Toyota", model="Corolla",
            plate="ABC123", mileage=0,
        )
        session.add_all([client, vehicle])
        session.commit()
        service = ClientService(AsyncAdapter(session))
        yield service, session, filial_id, client, vehicle


def _inspection(vehicle_id, mileage, created_at, service_order_id=None):
    return PreliminaryInspection(
        id=uuid.uuid4(),
        filial_id=uuid.uuid4(),
        vehicle_id=vehicle_id,
        inspector_user_id=uuid.uuid4(),
        service_order_id=service_order_id,
        mileage=mileage,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_vehicle_with_no_inspections_has_no_current_mileage(env):
    service, _session, _filial_id, client, _vehicle = env

    read_client = await service.get_client(client.id)

    vehicle_read = read_client.vehicles[0]
    assert vehicle_read.mileage == 0
    assert vehicle_read.current_mileage is None
    assert vehicle_read.current_mileage_visit_date is None
    assert vehicle_read.current_mileage_service_order_id is None
    assert vehicle_read.current_mileage_service_order_code is None


@pytest.mark.asyncio
async def test_inspection_without_mileage_is_ignored(env):
    service, session, _filial_id, client, vehicle = env
    session.add(_inspection(vehicle.id, None, datetime(2026, 1, 1, tzinfo=UTC)))
    session.commit()

    read_client = await service.get_client(client.id)

    assert read_client.vehicles[0].current_mileage is None


@pytest.mark.asyncio
async def test_current_mileage_picks_the_most_recent_visit(env):
    service, session, _filial_id, client, vehicle = env
    session.add(_inspection(vehicle.id, 10_000, datetime(2026, 1, 1, tzinfo=UTC)))
    session.add(_inspection(vehicle.id, 15_000, datetime(2026, 6, 1, tzinfo=UTC)))
    session.commit()

    read_client = await service.get_client(client.id)

    vehicle_read = read_client.vehicles[0]
    assert vehicle_read.current_mileage == 15_000
    assert vehicle_read.current_mileage_visit_date == datetime(2026, 6, 1).date()
    assert vehicle_read.mileage == 0  # registration value untouched


@pytest.mark.asyncio
async def test_current_mileage_links_to_its_service_order(env):
    service, session, filial_id, client, vehicle = env
    order = ServiceOrder(
        id=uuid.uuid4(), filial_id=filial_id, sequence_number=2041, vehicle_id=vehicle.id
    )
    session.add(order)
    session.add(_inspection(vehicle.id, 20_000, datetime(2026, 6, 1, tzinfo=UTC), order.id))
    session.commit()

    read_client = await service.get_client(client.id)

    vehicle_read = read_client.vehicles[0]
    assert vehicle_read.current_mileage_service_order_id == order.id
    assert vehicle_read.current_mileage_service_order_code == "ODS-2041"


@pytest.mark.asyncio
async def test_no_fallback_to_an_older_order_linked_inspection(env):
    service, session, filial_id, client, vehicle = env
    order = ServiceOrder(
        id=uuid.uuid4(), filial_id=filial_id, sequence_number=2041, vehicle_id=vehicle.id
    )
    session.add(order)
    session.add(_inspection(vehicle.id, 10_000, datetime(2026, 1, 1, tzinfo=UTC), order.id))
    session.add(_inspection(vehicle.id, 20_000, datetime(2026, 6, 1, tzinfo=UTC)))  # no order yet
    session.commit()

    read_client = await service.get_client(client.id)

    vehicle_read = read_client.vehicles[0]
    assert vehicle_read.current_mileage == 20_000
    assert vehicle_read.current_mileage_service_order_id is None
    assert vehicle_read.current_mileage_service_order_code is None


@pytest.mark.asyncio
async def test_list_clients_resolves_mileage_for_every_client_in_one_pass(env):
    service, session, filial_id, client, vehicle = env
    other_client = Client(
        id=uuid.uuid4(),
        filial_id=filial_id,
        full_name="Otro Cliente",
        client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V,
        document_number="87654321",
        phone_primary="04121234568",
        address="Caracas",
    )
    other_vehicle = Vehicle(
        id=uuid.uuid4(), client_id=other_client.id, brand="Ford", model="Fiesta", plate="XYZ987"
    )
    session.add_all([other_client, other_vehicle])
    session.add(_inspection(vehicle.id, 30_000, datetime(2026, 6, 1, tzinfo=UTC)))
    session.commit()

    clients = await service.list_clients(filial_id)

    by_plate = {v.plate: v for c in clients for v in c.vehicles}
    assert by_plate["ABC123"].current_mileage == 30_000
    assert by_plate["XYZ987"].current_mileage is None
