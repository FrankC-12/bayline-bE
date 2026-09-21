"""Regression test: real Venezuelan plates are 6-8 characters depending on
type (see frontend/src/lib/venezuela-plate.ts), not exactly 8. The backend's
VehicleInput.validate_plate used to hard-require 8 characters, which
rejected every normal "particular" plate (e.g. "AB123CD", 7 chars) when
creating a client with a vehicle in the same request."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.schemas import ClientCreate, ClientUpdate, VehicleInput
from app.modules.clients.service import ClientService
from app.modules.filiales.models import Filial


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()
        yield ClientService(AsyncAdapter(session)), filial_id


@pytest.mark.asyncio
async def test_create_client_with_a_7_char_particular_plate(env):
    service, filial_id = env
    payload = ClientCreate(
        filial_id=filial_id,
        full_name="José Ramírez",
        client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V,
        document_number="12345678",
        phone_primary="04121234567",
        address="Caracas",
        vehicles=[VehicleInput(brand="Toyota", model="Hilux", plate="AB123CD")],
    )

    client = await service.create_client(payload)

    read_client = await service.get_client(client.id)
    assert read_client.vehicles[0].plate == "AB123CD"


def test_short_plates_are_not_rejected_by_the_schema():
    # "poder público" plates can be as short as 2 characters (e.g. "01").
    vehicle = VehicleInput(brand="Toyota", model="Hilux", plate="01")
    assert vehicle.plate == "01"


def test_blank_or_missing_plate_means_sin_placa():
    # A vehicle not yet registered has no plate at all — "sin placa" is a
    # valid, permanent state, not a validation failure.
    assert VehicleInput(brand="Toyota", model="Hilux", plate="   ").plate is None
    assert VehicleInput(brand="Toyota", model="Hilux", plate=None).plate is None
    assert VehicleInput(brand="Toyota", model="Hilux").plate is None


@pytest.mark.asyncio
async def test_create_client_with_no_plate_then_resave_unchanged(env):
    """Saving a vehicle with no plate persists it as NULL, and resaving the
    client without touching anything (the exact payload round-tripped back)
    must succeed — this is the "abrir y guardar sin tocar nada" scenario."""
    service, filial_id = env
    payload = ClientCreate(
        filial_id=filial_id,
        full_name="Andrés Kook",
        client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V,
        document_number="87654321",
        phone_primary="04121234567",
        address="Caracas",
        vehicles=[VehicleInput(brand="Toyota", model="Hilux", plate=None)],
    )
    client = await service.create_client(payload)
    read_client = await service.get_client(client.id)
    assert read_client.vehicles[0].plate is None

    vehicle_id = read_client.vehicles[0].id
    resaved = await service.update_client(
        client.id,
        ClientUpdate(
            vehicles=[VehicleInput(id=vehicle_id, brand="Toyota", model="Hilux", plate=None)]
        ),
    )
    assert resaved.vehicles[0].plate is None


@pytest.mark.asyncio
async def test_resaving_a_legacy_nonstandard_plate_unchanged_still_persists(env):
    """A plate stored before the current format rules existed (or one that
    just doesn't match any known pattern) must not block a save that
    doesn't touch it — only the frontend's format check is skipped for an
    unchanged value; the backend itself never enforced a format regex."""
    service, filial_id = env
    payload = ClientCreate(
        filial_id=filial_id,
        full_name="Cliente Antiguo",
        client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V,
        document_number="11223344",
        phone_primary="04121234567",
        address="Caracas",
        vehicles=[VehicleInput(brand="Ford", model="Fiesta", plate="XYZ999O")],
    )
    client = await service.create_client(payload)
    read_client = await service.get_client(client.id)
    assert read_client.vehicles[0].plate == "XYZ999O"

    resaved = await service.update_client(
        client.id,
        ClientUpdate(
            vehicles=[
                VehicleInput(
                    id=read_client.vehicles[0].id, brand="Ford", model="Fiesta", plate="XYZ999O"
                )
            ]
        ),
    )
    assert resaved.vehicles[0].plate == "XYZ999O"
