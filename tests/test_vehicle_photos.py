"""Vehicle catalog photos — DealershipVehicle.images already existed as a
column but had no service method wired to it before this. add_vehicle_photos
appends (never replaces), same pattern as PartReturn.photo_urls and the
Inspecciones general-photos gallery."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.concesionario.enums import VehicleCondition, VehicleLocation
from app.modules.concesionario.exceptions import VehicleNotFoundError
from app.modules.concesionario.schemas import VehicleCreate
from app.modules.concesionario.service import ConcesionarioService
from app.modules.filiales.models import Filial


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Concesionario", slug="conce"))
        session.commit()
        db = AsyncAdapter(session)
        yield ConcesionarioService(db), filial_id


def _create_payload(filial_id, **overrides) -> VehicleCreate:
    data = dict(
        filial_id=filial_id, condition=VehicleCondition.NUEVO, location=VehicleLocation.PATIO,
        brand="Toyota", model="Corolla", year=2026, vin=str(uuid.uuid4())[:17], sku="SKU-1",
        price_cash=10_000, price_financed=11_000, cost_price=8_000,
    )
    data.update(overrides)
    return VehicleCreate(**data)


@pytest.mark.asyncio
async def test_new_vehicle_has_no_photos_by_default(env):
    service, filial_id = env
    vehicle = await service.create_vehicle(_create_payload(filial_id))
    assert vehicle.images == []


@pytest.mark.asyncio
async def test_add_vehicle_photos_appends_rather_than_replaces(env):
    service, filial_id = env
    vehicle = await service.create_vehicle(_create_payload(filial_id))

    once = await service.add_vehicle_photos(vehicle.id, ["https://bucket/a.jpg"])
    assert once.images == ["https://bucket/a.jpg"]

    twice = await service.add_vehicle_photos(vehicle.id, ["https://bucket/b.jpg", "https://bucket/c.jpg"])
    assert twice.images == ["https://bucket/a.jpg", "https://bucket/b.jpg", "https://bucket/c.jpg"]


@pytest.mark.asyncio
async def test_add_photos_to_unknown_vehicle_raises(env):
    service, _filial_id = env
    with pytest.raises(VehicleNotFoundError):
        await service.add_vehicle_photos(uuid.uuid4(), ["https://bucket/a.jpg"])


@pytest.mark.asyncio
async def test_remove_vehicle_photo_drops_only_that_url(env):
    service, filial_id = env
    vehicle = await service.create_vehicle(_create_payload(filial_id))
    await service.add_vehicle_photos(vehicle.id, ["https://bucket/a.jpg", "https://bucket/b.jpg"])

    updated = await service.remove_vehicle_photo(vehicle.id, "https://bucket/a.jpg")

    assert updated.images == ["https://bucket/b.jpg"]


@pytest.mark.asyncio
async def test_removing_a_url_not_in_the_list_is_a_no_op(env):
    service, filial_id = env
    vehicle = await service.create_vehicle(_create_payload(filial_id))
    await service.add_vehicle_photos(vehicle.id, ["https://bucket/a.jpg"])

    updated = await service.remove_vehicle_photo(vehicle.id, "https://bucket/does-not-exist.jpg")

    assert updated.images == ["https://bucket/a.jpg"]


@pytest.mark.asyncio
async def test_remove_photo_from_unknown_vehicle_raises(env):
    service, _filial_id = env
    with pytest.raises(VehicleNotFoundError):
        await service.remove_vehicle_photo(uuid.uuid4(), "https://bucket/a.jpg")
