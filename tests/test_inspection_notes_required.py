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
        yield InspectionService(AsyncAdapter(session)), session


def _create_payload(**overrides):
    fields = {"filial_id": uuid.uuid4(), "vehicle_id": uuid.uuid4()}
    fields.update(overrides)
    return InspectionCreate(**fields)


@pytest.mark.asyncio
async def test_creating_a_completed_inspection_without_notes_is_rejected(env):
    service, _session = env
    with pytest.raises(InspectionNotesRequiredError):
        await service.create_inspection(_create_payload(notes=None), uuid.uuid4())


@pytest.mark.asyncio
async def test_creating_a_completed_inspection_with_only_whitespace_notes_is_rejected(env):
    service, _session = env
    with pytest.raises(InspectionNotesRequiredError):
        await service.create_inspection(_create_payload(notes="   "), uuid.uuid4())


@pytest.mark.asyncio
async def test_creating_a_completed_inspection_with_notes_succeeds(env):
    service, _session = env
    inspection = await service.create_inspection(
        _create_payload(notes="Ruido en frenos delanteros"), uuid.uuid4()
    )
    assert inspection.notes == "Ruido en frenos delanteros"
    assert inspection.status == InspectionStatus.COMPLETADA


@pytest.mark.asyncio
async def test_creating_an_in_process_inspection_without_notes_is_allowed(env):
    service, _session = env
    inspection = await service.create_inspection(
        _create_payload(notes=None, status=InspectionStatus.EN_PROCESO), uuid.uuid4()
    )
    assert inspection.status == InspectionStatus.EN_PROCESO


@pytest.mark.asyncio
async def test_finishing_an_in_process_inspection_without_notes_is_rejected(env):
    service, _session = env
    inspection = await service.create_inspection(
        _create_payload(notes=None, status=InspectionStatus.EN_PROCESO), uuid.uuid4()
    )
    with pytest.raises(InspectionNotesRequiredError):
        await service.update_inspection(
            inspection.id, InspectionUpdate(status=InspectionStatus.COMPLETADA)
        )


@pytest.mark.asyncio
async def test_finishing_an_in_process_inspection_with_notes_succeeds(env):
    service, _session = env
    inspection = await service.create_inspection(
        _create_payload(notes=None, status=InspectionStatus.EN_PROCESO), uuid.uuid4()
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
    service, session = env
    inspection = PreliminaryInspection(
        filial_id=uuid.uuid4(), vehicle_id=uuid.uuid4(), inspector_user_id=uuid.uuid4(),
        status=InspectionStatus.COMPLETADA, notes=None,
    )
    session.add(inspection)
    session.flush()

    updated = await service.update_inspection(inspection.id, InspectionUpdate(mileage=20000))
    assert updated.mileage == 20000
