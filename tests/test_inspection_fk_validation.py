"""Regression for a 500 reported from the field: the mobile app posted a
POST /inspections payload with a vehicle_id that didn't exist in this
filial's `vehicles` table. In production (Postgres, FK constraints
enforced) that raised an unhandled IntegrityError, which the generic
exception handler turned into an opaque 500 instead of a clean 404.
SQLite (used by the rest of this test suite) doesn't enforce FK
constraints by default, so nothing caught this earlier — hence an
explicit existence check in the service, tested here independently of
whatever DB backend is running."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.exceptions import VehicleNotFoundError
from app.modules.clients.models import Vehicle
from app.modules.filiales.exceptions import FilialNotFoundError
from app.modules.filiales.models import Filial
from app.modules.inspections.schemas import InspectionCreate
from app.modules.inspections.service import InspectionService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial = Filial(holding_id=uuid.uuid4(), name="Taller Central", slug="taller-central")
        vehicle = Vehicle(client_id=uuid.uuid4(), brand="Toyota", model="Corolla")
        session.add_all([filial, vehicle])
        session.flush()
        yield InspectionService(AsyncAdapter(session)), filial.id, vehicle.id


@pytest.mark.asyncio
async def test_creating_an_inspection_for_an_unknown_vehicle_raises_a_clean_404(env):
    service, filial_id, _vehicle_id = env
    payload = InspectionCreate(filial_id=filial_id, vehicle_id=uuid.uuid4(), notes="Chequeo de ingreso")
    with pytest.raises(VehicleNotFoundError):
        await service.create_inspection(payload, uuid.uuid4())


@pytest.mark.asyncio
async def test_creating_an_inspection_for_an_unknown_filial_raises_a_clean_404(env):
    service, _filial_id, vehicle_id = env
    payload = InspectionCreate(filial_id=uuid.uuid4(), vehicle_id=vehicle_id, notes="Chequeo de ingreso")
    with pytest.raises(FilialNotFoundError):
        await service.create_inspection(payload, uuid.uuid4())


@pytest.mark.asyncio
async def test_creating_an_inspection_with_valid_filial_and_vehicle_succeeds(env):
    service, filial_id, vehicle_id = env
    payload = InspectionCreate(filial_id=filial_id, vehicle_id=vehicle_id, notes="Chequeo de ingreso")
    inspection = await service.create_inspection(payload, uuid.uuid4())
    assert inspection.filial_id == filial_id
    assert inspection.vehicle_id == vehicle_id
