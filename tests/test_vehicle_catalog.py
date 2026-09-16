"""Holding-wide vehicle brand/model catalog: seeded per holding, names unique
within their scope (brand within a holding, model within a brand), and
strictly tenant-isolated (a brand/model from another holding is a 404, not
an update)."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.vehicle_catalog.exceptions import (
    VehicleBrandNameAlreadyExistsError,
    VehicleBrandNotFoundError,
    VehicleModelNameAlreadyExistsError,
    VehicleModelNotFoundError,
)
from app.modules.vehicle_catalog.schemas import (
    VehicleBrandCreate,
    VehicleBrandUpdate,
    VehicleModelCreate,
)
from app.modules.vehicle_catalog.service import DEFAULT_BRANDS, VehicleCatalogService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield VehicleCatalogService(AsyncAdapter(session)), session


@pytest.mark.asyncio
async def test_seed_default_brands_creates_all_twelve(env):
    service, _session = env
    holding_id = uuid.uuid4()

    await service.seed_default_brands(holding_id)

    brands = await service.list_brands(holding_id)
    assert {b.name for b in brands} == set(DEFAULT_BRANDS)
    assert all(b.models == [] for b in brands)


@pytest.mark.asyncio
async def test_brands_are_scoped_per_holding(env):
    service, _session = env
    holding_a, holding_b = uuid.uuid4(), uuid.uuid4()
    await service.seed_default_brands(holding_a)

    assert await service.list_brands(holding_b) == []


@pytest.mark.asyncio
async def test_duplicate_brand_name_within_holding_is_rejected(env):
    service, _session = env
    holding_id = uuid.uuid4()
    await service.create_brand(holding_id, VehicleBrandCreate(name="Toyota"))

    with pytest.raises(VehicleBrandNameAlreadyExistsError):
        await service.create_brand(holding_id, VehicleBrandCreate(name="toyota"))


@pytest.mark.asyncio
async def test_same_brand_name_allowed_in_different_holdings(env):
    service, _session = env
    holding_a, holding_b = uuid.uuid4(), uuid.uuid4()
    await service.create_brand(holding_a, VehicleBrandCreate(name="Toyota"))

    brand = await service.create_brand(holding_b, VehicleBrandCreate(name="Toyota"))
    assert brand.name == "Toyota"


@pytest.mark.asyncio
async def test_create_and_list_models_under_a_brand(env):
    service, _session = env
    holding_id = uuid.uuid4()
    brand = await service.create_brand(holding_id, VehicleBrandCreate(name="Toyota"))

    await service.create_model(brand.id, holding_id, VehicleModelCreate(name="Hilux"))
    await service.create_model(brand.id, holding_id, VehicleModelCreate(name="Corolla"))

    refreshed = await service.get_brand(brand.id, holding_id)
    assert [m.name for m in refreshed.models] == ["Corolla", "Hilux"]


@pytest.mark.asyncio
async def test_duplicate_model_name_within_brand_is_rejected(env):
    service, _session = env
    holding_id = uuid.uuid4()
    brand = await service.create_brand(holding_id, VehicleBrandCreate(name="Toyota"))
    await service.create_model(brand.id, holding_id, VehicleModelCreate(name="Hilux"))

    with pytest.raises(VehicleModelNameAlreadyExistsError):
        await service.create_model(brand.id, holding_id, VehicleModelCreate(name="hilux"))


@pytest.mark.asyncio
async def test_same_model_name_allowed_under_different_brands(env):
    service, _session = env
    holding_id = uuid.uuid4()
    toyota = await service.create_brand(holding_id, VehicleBrandCreate(name="Toyota"))
    ford = await service.create_brand(holding_id, VehicleBrandCreate(name="Ford"))

    await service.create_model(toyota.id, holding_id, VehicleModelCreate(name="Ranger"))
    model = await service.create_model(ford.id, holding_id, VehicleModelCreate(name="Ranger"))
    assert model.name == "Ranger"


@pytest.mark.asyncio
async def test_cannot_add_a_model_to_another_holdings_brand(env):
    service, _session = env
    holding_a, holding_b = uuid.uuid4(), uuid.uuid4()
    brand = await service.create_brand(holding_a, VehicleBrandCreate(name="Toyota"))

    with pytest.raises(VehicleBrandNotFoundError):
        await service.create_model(brand.id, holding_b, VehicleModelCreate(name="Hilux"))


@pytest.mark.asyncio
async def test_deactivated_brand_is_excluded_by_default_but_listable(env):
    service, _session = env
    holding_id = uuid.uuid4()
    brand = await service.create_brand(holding_id, VehicleBrandCreate(name="Toyota"))

    await service.set_brand_active(brand.id, holding_id, is_active=False)

    assert await service.list_brands(holding_id) == []
    all_brands = await service.list_brands(holding_id, include_inactive=True)
    assert len(all_brands) == 1
    assert all_brands[0].is_active is False


@pytest.mark.asyncio
async def test_rename_brand_checks_new_name_uniqueness(env):
    service, _session = env
    holding_id = uuid.uuid4()
    await service.create_brand(holding_id, VehicleBrandCreate(name="Toyota"))
    ford = await service.create_brand(holding_id, VehicleBrandCreate(name="Ford"))

    with pytest.raises(VehicleBrandNameAlreadyExistsError):
        await service.update_brand(ford.id, holding_id, VehicleBrandUpdate(name="Toyota"))
