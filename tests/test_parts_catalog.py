"""Parts catalog: categories/measures are holding-wide catalogs (same pattern
as VehicleBrand), a part's vehicle fit (brand/model/year range) is optional
and validated when present, and parts are deactivated, never deleted."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.filiales.models import Filial
from app.modules.parts.exceptions import (
    PartCategoryNameAlreadyExistsError,
    VehicleModelBrandMismatchError,
)
from app.modules.parts.schemas import (
    PartBulkItem,
    PartCategoryCreate,
    PartCreate,
    PartUpdate,
)
from app.modules.parts.service import PartsService
from app.modules.vehicle_catalog.models import VehicleBrand, VehicleModel


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        holding_id = uuid.uuid4()
        filial = Filial(id=uuid.uuid4(), holding_id=holding_id, name="Taller Test", slug="taller-test")
        session.add(filial)
        session.commit()
        yield PartsService(AsyncAdapter(session)), session, filial.id, holding_id


def _create_category(service, holding_id, name="Lubricantes"):
    return service.create_category(holding_id, PartCategoryCreate(name=name))


@pytest.mark.asyncio
async def test_create_part_requires_a_category(env):
    service, _session, filial_id, holding_id = env
    category = await _create_category(service, holding_id)

    part = await service.create_part(
        PartCreate(filial_id=filial_id, code="P-1", name="Aceite 5W-30", category_id=category.id, unit="Litro")
    )

    assert part.category_name == "Lubricantes"
    assert part.vehicle_brand_id is None
    assert part.year_from is None
    assert part.is_active is True


@pytest.mark.asyncio
async def test_universal_part_has_no_vehicle_fit(env):
    service, _session, filial_id, holding_id = env
    category = await _create_category(service, holding_id, "Lubricantes")

    part = await service.create_part(
        PartCreate(filial_id=filial_id, code="P-1", name="Grasa universal", category_id=category.id, unit="Kg")
    )

    assert part.vehicle_brand_id is None
    assert part.vehicle_model_id is None
    assert part.year_from is None
    assert part.year_to is None


@pytest.mark.asyncio
async def test_part_with_matching_brand_and_model_is_accepted(env):
    service, session, filial_id, holding_id = env
    category = await _create_category(service, holding_id)
    brand = VehicleBrand(holding_id=holding_id, name="Toyota")
    session.add(brand)
    session.flush()
    model = VehicleModel(brand_id=brand.id, name="Hilux")
    session.add(model)
    session.commit()

    part = await service.create_part(
        PartCreate(
            filial_id=filial_id,
            code="P-2",
            name="Filtro de aceite",
            category_id=category.id,
            vehicle_brand_id=brand.id,
            vehicle_model_id=model.id,
            year_from=2018,
            year_to=2024,
            unit="Unidad",
        )
    )

    assert part.vehicle_brand_name == "Toyota"
    assert part.vehicle_model_name == "Hilux"
    assert part.year_from == 2018
    assert part.year_to == 2024


@pytest.mark.asyncio
async def test_model_from_a_different_brand_is_rejected(env):
    service, session, filial_id, holding_id = env
    category = await _create_category(service, holding_id)
    toyota = VehicleBrand(holding_id=holding_id, name="Toyota")
    ford = VehicleBrand(holding_id=holding_id, name="Ford")
    session.add_all([toyota, ford])
    session.flush()
    ranger = VehicleModel(brand_id=ford.id, name="Ranger")
    session.add(ranger)
    session.commit()

    with pytest.raises(VehicleModelBrandMismatchError):
        await service.create_part(
            PartCreate(
                filial_id=filial_id, code="P-3", name="Repuesto", category_id=category.id,
                vehicle_brand_id=toyota.id, vehicle_model_id=ranger.id, unit="Unidad",
            )
        )


@pytest.mark.asyncio
async def test_model_without_a_brand_is_rejected(env):
    service, session, filial_id, holding_id = env
    category = await _create_category(service, holding_id)
    brand = VehicleBrand(holding_id=holding_id, name="Toyota")
    session.add(brand)
    session.flush()
    model = VehicleModel(brand_id=brand.id, name="Hilux")
    session.add(model)
    session.commit()

    with pytest.raises(VehicleModelBrandMismatchError):
        await service.create_part(
            PartCreate(
                filial_id=filial_id, code="P-4", name="Repuesto", category_id=category.id,
                vehicle_model_id=model.id, unit="Unidad",
            )
        )


@pytest.mark.asyncio
async def test_year_from_after_year_to_is_rejected(env):
    service, _session, filial_id, holding_id = env
    category = await _create_category(service, holding_id)

    with pytest.raises(BadRequestError):
        await service.create_part(
            PartCreate(
                filial_id=filial_id, code="P-5", name="Repuesto", category_id=category.id,
                year_from=2024, year_to=2018, unit="Unidad",
            )
        )


@pytest.mark.asyncio
async def test_deactivated_part_is_excluded_by_default_but_listable(env):
    service, _session, filial_id, holding_id = env
    category = await _create_category(service, holding_id)
    part = await service.create_part(
        PartCreate(filial_id=filial_id, code="P-6", name="Repuesto", category_id=category.id, unit="Unidad")
    )

    await service.set_part_active(part.id, is_active=False)

    active_only = await service.list_parts(filial_id)
    assert active_only == []
    everything = await service.list_parts(filial_id, include_inactive=True)
    assert len(everything) == 1
    assert everything[0].is_active is False


@pytest.mark.asyncio
async def test_update_part_can_clear_vehicle_fit(env):
    service, session, filial_id, holding_id = env
    category = await _create_category(service, holding_id)
    brand = VehicleBrand(holding_id=holding_id, name="Toyota")
    session.add(brand)
    session.commit()
    part = await service.create_part(
        PartCreate(
            filial_id=filial_id, code="P-7", name="Repuesto", category_id=category.id,
            vehicle_brand_id=brand.id, year_from=2018, unit="Unidad",
        )
    )

    updated = await service.update_part(
        part.id, PartUpdate(clear_vehicle_brand=True, clear_years=True)
    )

    assert updated.vehicle_brand_id is None
    assert updated.year_from is None


@pytest.mark.asyncio
async def test_category_name_is_unique_per_holding_case_insensitive(env):
    service, _session, _filial_id, holding_id = env
    await _create_category(service, holding_id, "Frenos")

    with pytest.raises(PartCategoryNameAlreadyExistsError):
        await _create_category(service, holding_id, "frenos")


@pytest.mark.asyncio
async def test_same_category_name_allowed_in_different_holdings(env):
    service, _session, _filial_id, holding_id = env
    other_holding_id = uuid.uuid4()
    await _create_category(service, holding_id, "Frenos")

    category = await _create_category(service, other_holding_id, "Frenos")
    assert category.name == "Frenos"


@pytest.mark.asyncio
async def test_get_or_create_category_reuses_existing_by_name(env):
    service, _session, _filial_id, holding_id = env
    created = await _create_category(service, holding_id, "Suspensión")

    resolved = await service.get_or_create_category(holding_id, "suspensión")

    assert resolved.id == created.id


@pytest.mark.asyncio
async def test_bulk_create_parts_resolves_category_by_name_and_skips_duplicates(env):
    service, _session, filial_id, holding_id = env
    await service.create_part(
        PartCreate(
            filial_id=filial_id, code="P-8", name="Existente",
            category_id=(await _create_category(service, holding_id, "Motor")).id, unit="Unidad",
        )
    )

    created, skipped = await service.bulk_create_parts(
        filial_id,
        [
            PartBulkItem(code="P-8", name="Duplicado", category="Motor", unit="Unidad"),
            PartBulkItem(code="P-9", name="Bujía", category="Motor", unit="Unidad"),
            PartBulkItem(code="P-10", name="Correa", category="Correas nuevas", unit="Unidad"),
        ],
    )

    assert skipped == ["P-8"]
    assert {p.code for p in created} == {"P-9", "P-10"}
    assert created[0].category_name in {"Motor", "Correas nuevas"}
