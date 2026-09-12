"""Maintenance plan catalog (MPT-style) — a brand's schedule of Temparios at
given km/month intervals, living next to the Tempario catalog in Post Ventas."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import Tempario
from app.modules.post_ventas.schemas import (
    MaintenancePlanCreate,
    MaintenancePlanEntryInput,
    MaintenancePlanUpdate,
)
from app.modules.post_ventas.service import PostVentasService


@pytest.fixture
def catalog():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        other_filial_id = uuid.uuid4()

        oil_change = Tempario(
            filial_id=filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=501,
            name="Cambio de aceite y filtro",
            estimated_hours=1,
        )
        tire_rotation = Tempario(
            filial_id=filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=502,
            name="Rotación de neumáticos",
            estimated_hours=0.5,
        )
        foreign_tempario = Tempario(
            filial_id=other_filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=1,
            name="De otra filial",
            estimated_hours=1,
        )
        session.add_all([oil_change, tire_rotation, foreign_tempario])
        session.commit()
        yield session, filial_id, oil_change, tire_rotation, foreign_tempario
    engine.dispose()


@pytest.mark.asyncio
async def test_create_plan_persists_entries_with_denormalized_tempario_info(catalog):
    session, filial_id, oil_change, tire_rotation, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))

    plan = await service.create_plan(
        MaintenancePlanCreate(
            filial_id=filial_id,
            brand="Toyota",
            name="Plan de mantenimiento programado",
            entries=[
                MaintenancePlanEntryInput(tempario_id=oil_change.id, interval_km=5000),
                MaintenancePlanEntryInput(tempario_id=tire_rotation.id, interval_months=6),
            ],
        )
    )

    assert plan.brand == "Toyota"
    assert len(plan.entries) == 2
    by_tempario = {e.tempario_id: e for e in plan.entries}
    assert by_tempario[oil_change.id].tempario_code == "MP-501"
    assert by_tempario[oil_change.id].interval_km == 5000
    assert by_tempario[tire_rotation.id].tempario_name == "Rotación de neumáticos"
    assert by_tempario[tire_rotation.id].interval_months == 6


@pytest.mark.asyncio
async def test_create_plan_rejects_tempario_from_another_filial(catalog):
    session, filial_id, _oil_change, _tire_rotation, foreign_tempario = catalog
    service = PostVentasService(AsyncAdapter(session))

    with pytest.raises(BadRequestError):
        await service.create_plan(
            MaintenancePlanCreate(
                filial_id=filial_id,
                brand="Toyota",
                name="Plan inválido",
                entries=[MaintenancePlanEntryInput(tempario_id=foreign_tempario.id, interval_km=5000)],
            )
        )


@pytest.mark.asyncio
async def test_update_plan_replaces_entries(catalog):
    session, filial_id, oil_change, tire_rotation, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))
    plan = await service.create_plan(
        MaintenancePlanCreate(
            filial_id=filial_id,
            brand="Toyota",
            name="Plan",
            entries=[MaintenancePlanEntryInput(tempario_id=oil_change.id, interval_km=5000)],
        )
    )

    updated = await service.update_plan(
        plan.id,
        MaintenancePlanUpdate(
            entries=[MaintenancePlanEntryInput(tempario_id=tire_rotation.id, interval_months=6)]
        ),
    )

    assert len(updated.entries) == 1
    assert updated.entries[0].tempario_id == tire_rotation.id
    assert updated.brand == "Toyota"  # Untouched fields are preserved.


@pytest.mark.asyncio
async def test_list_plans_filters_by_search_and_scopes_by_filial(catalog):
    session, filial_id, oil_change, _tire_rotation, _foreign = catalog
    other_filial_id = uuid.uuid4()
    service = PostVentasService(AsyncAdapter(session))
    await service.create_plan(
        MaintenancePlanCreate(filial_id=filial_id, brand="Toyota", name="Plan Toyota", entries=[])
    )
    await service.create_plan(
        MaintenancePlanCreate(filial_id=filial_id, brand="Lexus", name="Plan Lexus", entries=[])
    )

    all_plans = await service.list_plans(filial_id)
    assert {p.brand for p in all_plans} == {"Toyota", "Lexus"}

    filtered = await service.list_plans(filial_id, search="lexus")
    assert [p.brand for p in filtered] == ["Lexus"]

    assert await service.list_plans(other_filial_id) == []


@pytest.mark.asyncio
async def test_entries_are_sorted_nearest_due_first(catalog):
    session, filial_id, oil_change, tire_rotation, _foreign = catalog
    service = PostVentasService(AsyncAdapter(session))

    plan = await service.create_plan(
        MaintenancePlanCreate(
            filial_id=filial_id,
            brand="Toyota",
            name="Plan",
            entries=[
                MaintenancePlanEntryInput(tempario_id=tire_rotation.id, interval_km=10000),
                MaintenancePlanEntryInput(tempario_id=oil_change.id, interval_km=5000),
            ],
        )
    )

    assert [e.tempario_id for e in plan.entries] == [oil_change.id, tire_rotation.id]


def test_entry_requires_at_least_one_interval():
    with pytest.raises(ValidationError):
        MaintenancePlanEntryInput(tempario_id=uuid.uuid4())
