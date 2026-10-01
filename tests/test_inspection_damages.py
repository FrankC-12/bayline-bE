"""Mapa de daños — each tapped point on the vehicle diagram is persisted as
an InspectionDamage row linked to its PreliminaryInspection. Reading an
inspection back (list, get, get_for_order) must eager-load `damages` since
it's a lazy relationship by default — missing that eager-load would raise
MissingGreenletError the moment InspectionRead tries to serialize it."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.inspections.exceptions import InspectionDamageNotFoundError
from app.modules.inspections.schemas import InspectionCreate, InspectionDamageInput
from app.modules.inspections.service import InspectionService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield InspectionService(AsyncAdapter(session)), session


def _create_payload(**overrides):
    fields = {
        "filial_id": uuid.uuid4(), "vehicle_id": uuid.uuid4(), "notes": "Chequeo de ingreso",
    }
    fields.update(overrides)
    return InspectionCreate(**fields)


def _damage(**overrides):
    fields = dict(x=0.72, y=0.60, zone="puerta_trasera_derecha", kind="rayon", severity="leve", description=None)
    fields.update(overrides)
    return InspectionDamageInput(**fields)


@pytest.mark.asyncio
async def test_create_inspection_persists_its_damages(env):
    service, _session = env
    inspection = await service.create_inspection(
        _create_payload(
            damages=[
                _damage(zone="puerta_trasera_derecha", kind="rayon", severity="leve"),
                _damage(x=0.28, y=0.21, zone="guardafango_delantero_izquierdo", kind="abolladura", severity="grave", description=None),
            ]
        ),
        uuid.uuid4(),
    )

    assert len(inspection.damages) == 2
    zones = {d.zone for d in inspection.damages}
    assert zones == {"puerta_trasera_derecha", "guardafango_delantero_izquierdo"}


@pytest.mark.asyncio
async def test_inspection_without_damages_in_payload_saves_empty_list(env):
    service, _session = env
    inspection = await service.create_inspection(_create_payload(), uuid.uuid4())
    assert inspection.damages == []


@pytest.mark.asyncio
async def test_list_inspections_eager_loads_damages(env):
    service, _session = env
    created = await service.create_inspection(
        _create_payload(damages=[_damage()]), uuid.uuid4()
    )

    listed = await service.list_inspections(created.filial_id)
    assert len(listed) == 1
    assert len(listed[0].damages) == 1
    assert listed[0].damages[0].zone == "puerta_trasera_derecha"


@pytest.mark.asyncio
async def test_get_inspection_eager_loads_damages(env):
    service, _session = env
    created = await service.create_inspection(
        _create_payload(damages=[_damage()]), uuid.uuid4()
    )

    fetched = await service.get_inspection(created.id)
    assert len(fetched.damages) == 1


@pytest.mark.asyncio
async def test_get_for_order_eager_loads_damages(env):
    service, session = env
    created = await service.create_inspection(
        _create_payload(damages=[_damage()]), uuid.uuid4()
    )
    order_id = uuid.uuid4()
    created.service_order_id = order_id
    session.commit()

    fetched = await service.get_for_order(order_id)
    assert fetched is not None
    assert len(fetched.damages) == 1


@pytest.mark.asyncio
async def test_deleting_an_unlinked_inspection_cascades_its_damages(env):
    service, session = env
    created = await service.create_inspection(
        _create_payload(damages=[_damage()]), uuid.uuid4()
    )
    damage_id = created.damages[0].id

    await service.delete_inspection(created.id)

    from app.modules.inspections.models import InspectionDamage

    assert session.get(InspectionDamage, damage_id) is None


@pytest.mark.asyncio
async def test_set_damage_photo_persists_the_url(env):
    service, _session = env
    created = await service.create_inspection(
        _create_payload(damages=[_damage()]), uuid.uuid4()
    )
    damage_id = created.damages[0].id

    updated = await service.set_damage_photo(created.id, damage_id, "https://bucket.s3.amazonaws.com/inspections/x.jpg")

    assert updated.damages[0].photo_url == "https://bucket.s3.amazonaws.com/inspections/x.jpg"


@pytest.mark.asyncio
async def test_set_damage_photo_for_unknown_damage_raises(env):
    service, _session = env
    created = await service.create_inspection(_create_payload(), uuid.uuid4())

    with pytest.raises(InspectionDamageNotFoundError):
        await service.set_damage_photo(created.id, uuid.uuid4(), "https://bucket.s3.amazonaws.com/x.jpg")


@pytest.mark.asyncio
async def test_add_inspection_photos_appends_rather_than_replaces(env):
    service, _session = env
    created = await service.create_inspection(_create_payload(), uuid.uuid4())

    once = await service.add_inspection_photos(created.id, ["https://bucket/a.jpg"])
    assert once.photo_urls == ["https://bucket/a.jpg"]

    twice = await service.add_inspection_photos(created.id, ["https://bucket/b.jpg"])
    assert twice.photo_urls == ["https://bucket/a.jpg", "https://bucket/b.jpg"]


@pytest.mark.asyncio
async def test_damages_are_returned_in_submission_order(env):
    """All damages in one inspection share the same created_at (same
    transaction) — sort_order, not created_at, is what the frontend relies
    on to match each returned damage id back to its own pending photo."""
    service, _session = env
    inspection = await service.create_inspection(
        _create_payload(
            damages=[
                _damage(zone="capo", kind="rayon"),
                _damage(zone="maletero", kind="golpe"),
                _damage(zone="techo", kind="oxido"),
            ]
        ),
        uuid.uuid4(),
    )

    assert [d.zone for d in inspection.damages] == ["capo", "maletero", "techo"]

    refetched = await service.get_inspection(inspection.id)
    assert [d.zone for d in refetched.damages] == ["capo", "maletero", "techo"]


@pytest.mark.asyncio
async def test_inspection_without_photos_defaults_to_empty_list(env):
    service, _session = env
    created = await service.create_inspection(_create_payload(), uuid.uuid4())
    assert created.photo_urls == []
