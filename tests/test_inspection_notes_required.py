"""Notes are the customer-reported reason/symptom that a new ODS will later
inherit verbatim from its linked PreliminaryInspection (see
test_service_order_creation.py). To guarantee there's always something to
inherit, notes become mandatory the moment an inspection is marked
completada — but only at that transition, so an inspection that was already
completada before this rule existed (and so has no notes) doesn't get
retroactively blocked from unrelated updates."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.models import Vehicle
from app.modules.filiales.models import Filial
from app.modules.inspections.enums import InspectionStatus
from app.modules.inspections.exceptions import InspectionNotesRequiredError
from app.modules.inspections.models import PreliminaryInspection
from app.modules.inspections.schemas import InspectionCreate, InspectionUpdate
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
        yield InspectionService(AsyncAdapter(session)), session, filial.id, vehicle.id


def _create_payload(filial_id, vehicle_id, **overrides):
    fields = {"filial_id": filial_id, "vehicle_id": vehicle_id}
    fields.update(overrides)
    return InspectionCreate(**fields)


@pytest.mark.asyncio
async def test_creating_a_completed_inspection_without_notes_is_rejected(env):
    service, _session, filial_id, vehicle_id = env
    with pytest.raises(InspectionNotesRequiredError):
        await service.create_inspection(_create_payload(filial_id, vehicle_id, notes=None), uuid.uuid4())


@pytest.mark.asyncio
async def test_creating_a_completed_inspection_with_only_whitespace_notes_is_rejected(env):
    service, _session, filial_id, vehicle_id = env
    with pytest.raises(InspectionNotesRequiredError):
        await service.create_inspection(_create_payload(filial_id, vehicle_id, notes="   "), uuid.uuid4())


@pytest.mark.asyncio
async def test_creating_a_completed_inspection_with_notes_succeeds(env):
    service, _session, filial_id, vehicle_id = env
    inspection = await service.create_inspection(
        _create_payload(filial_id, vehicle_id, notes="Ruido en frenos delanteros"), uuid.uuid4()
    )
    assert inspection.notes == "Ruido en frenos delanteros"
    assert inspection.status == InspectionStatus.COMPLETADA


@pytest.mark.asyncio
async def test_creating_an_in_process_inspection_without_notes_is_allowed(env):
    service, _session, filial_id, vehicle_id = env
    inspection = await service.create_inspection(
        _create_payload(filial_id, vehicle_id, notes=None, status=InspectionStatus.EN_PROCESO), uuid.uuid4()
    )
    assert inspection.status == InspectionStatus.EN_PROCESO


@pytest.mark.asyncio
async def test_finishing_an_in_process_inspection_without_notes_is_rejected(env):
    service, _session, filial_id, vehicle_id = env
    inspection = await service.create_inspection(
        _create_payload(filial_id, vehicle_id, notes=None, status=InspectionStatus.EN_PROCESO), uuid.uuid4()
    )
    with pytest.raises(InspectionNotesRequiredError):
        await service.update_inspection(
            inspection.id, InspectionUpdate(status=InspectionStatus.COMPLETADA)
        )


@pytest.mark.asyncio
async def test_finishing_an_in_process_inspection_with_notes_succeeds(env):
    service, _session, filial_id, vehicle_id = env
    inspection = await service.create_inspection(
        _create_payload(filial_id, vehicle_id, notes=None, status=InspectionStatus.EN_PROCESO), uuid.uuid4()
    )
    completed = await service.update_inspection(
        inspection.id,
        InspectionUpdate(status=InspectionStatus.COMPLETADA, notes="Vibración al frenar"),
    )
    assert completed.status == InspectionStatus.COMPLETADA
    assert completed.notes == "Vibración al frenar"


@pytest.mark.asyncio
async def test_a_legacy_completed_inspection_without_notes_can_still_be_updated(env):
    """Regression: this used to check the inspection's CURRENT status after
    applying the update, which wrongly blocked touching any other field
    (mileage, service_order_id, ...) on an inspection that was already
    completada — e.g. one created before this rule existed."""
    service, session, _filial_id, _vehicle_id = env
    inspection = PreliminaryInspection(
        filial_id=uuid.uuid4(), vehicle_id=uuid.uuid4(), inspector_user_id=uuid.uuid4(),
        status=InspectionStatus.COMPLETADA, notes=None,
    )
    session.add(inspection)
    session.flush()

    updated = await service.update_inspection(inspection.id, InspectionUpdate(mileage=20000))
    assert updated.mileage == 20000
