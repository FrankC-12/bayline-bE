"""S3 · Ubicación física del vehículo de inventario — a new dealership
vehicle records where it physically sits (patio/showroom/sucursal), and that
location can be changed later without going through the status workflow."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario.enums import VehicleCondition, VehicleLocation, VehicleStatus
from app.modules.concesionario.models import DealershipVehicle
from app.modules.concesionario.schemas import VehicleCreate, VehicleUpdate
from app.modules.concesionario.service import ConcesionarioService
from app.modules.filiales.models import Filial
from app.modules.roles.enums import RoleScope


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Concesionario", slug="conce"))
        session.commit()
        db = AsyncAdapter(session)
        current_user = CurrentUser(
            user_id=uuid.uuid4(), email="user@test.com", role_id=uuid.uuid4(), role_slug="vendedor",
            scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
        )
        yield ConcesionarioService(db), session, filial_id, current_user


def _create_payload(filial_id, **overrides) -> VehicleCreate:
    data = dict(
        filial_id=filial_id, condition=VehicleCondition.NUEVO, location=VehicleLocation.PATIO,
        brand="Toyota", model="Corolla", year=2026, vin=str(uuid.uuid4())[:17], sku="SKU-1",
        price_cash=10_000, price_financed=11_000, cost_price=8_000,
    )
    data.update(overrides)
    return VehicleCreate(**data)


@pytest.mark.asyncio
async def test_create_vehicle_persists_its_location(env):
    service, _session, filial_id, _user = env
    vehicle = await service.create_vehicle(_create_payload(filial_id))
    assert vehicle.location == VehicleLocation.PATIO


@pytest.mark.asyncio
async def test_location_can_be_changed_without_touching_status(env):
    service, _session, filial_id, user = env
    vehicle = await service.create_vehicle(_create_payload(filial_id))
    assert vehicle.status == VehicleStatus.EN_TRANSITO

    updated = await service.update_vehicle(
        vehicle.id, VehicleUpdate(location=VehicleLocation.SHOWROOM), user
    )
    assert updated.location == VehicleLocation.SHOWROOM
    assert updated.status == VehicleStatus.EN_TRANSITO


@pytest.mark.asyncio
async def test_legacy_vehicle_with_no_location_is_left_null(env):
    """A row created before this field existed has no location — the update
    field loop only overwrites when a new value is actually supplied."""
    service, session, filial_id, user = env
    vehicle = DealershipVehicle(
        filial_id=filial_id, status=VehicleStatus.DISPONIBLE, condition=VehicleCondition.USADO,
        brand="Ford", model="Fiesta", year=2018, vin=str(uuid.uuid4())[:17], sku="SKU-2",
        price_cash=5_000, price_financed=5_500, cost_price=4_000,
    )
    session.add(vehicle)
    session.commit()
    assert vehicle.location is None

    updated = await service.update_vehicle(vehicle.id, VehicleUpdate(color="Rojo"), user)
    assert updated.location is None
