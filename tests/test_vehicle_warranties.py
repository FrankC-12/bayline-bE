"""Factory warranty per vehicle (VIN-keyed): auto-created when a dealership
sale closes, manually entered by VIN, or bulk-loaded — mirroring the Parts
bulk-import skip-duplicates pattern."""

import uuid
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.core.exceptions import ConflictError
from app.modules.concesionario.enums import SaleType, VehicleCondition, VehicleStatus
from app.modules.concesionario.models import DealershipVehicle
from app.modules.concesionario.schemas import VehicleSaleInput, VehicleUpdate
from app.modules.concesionario.service import ConcesionarioService
from app.modules.filiales.models import Filial
from app.modules.post_ventas.models import VehicleWarranty
from app.modules.post_ventas.schemas import (
    VehicleWarrantyBulkItem,
    VehicleWarrantyCreate,
)
from app.modules.post_ventas.service import PostVentasService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()
        db = AsyncAdapter(session)
        yield PostVentasService(db), session, filial_id


VIN = "1HGCM82633A004352"


@pytest.mark.asyncio
async def test_create_manual_warranty_computes_expiration(env):
    service, _session, filial_id = env
    warranty = await service.create_vehicle_warranty(
        VehicleWarrantyCreate(
            filial_id=filial_id, vin=VIN, brand="Toyota", model="Corolla",
            starts_at=date(2026, 1, 15), duration_months=36,
        ),
        None,
    )
    assert warranty.expires_at == date(2029, 1, 15)
    assert warranty.status == "vigente"
    assert warranty.source.value == "manual"


@pytest.mark.asyncio
async def test_manual_warranty_requires_a_duration():
    with pytest.raises(ValidationError):
        VehicleWarrantyCreate(filial_id=uuid.uuid4(), vin=VIN, brand="Toyota", starts_at=date.today())


@pytest.mark.asyncio
async def test_km_only_warranty_never_shows_as_vencida(env):
    service, _session, filial_id = env
    warranty = await service.create_vehicle_warranty(
        VehicleWarrantyCreate(
            filial_id=filial_id, vin=VIN, brand="Toyota", starts_at=date(2000, 1, 1), duration_km=100000,
        ),
        None,
    )
    assert warranty.expires_at is None
    assert warranty.status == "vigente"


@pytest.mark.asyncio
async def test_expired_warranty_shows_as_vencida(env):
    service, _session, filial_id = env
    warranty = await service.create_vehicle_warranty(
        VehicleWarrantyCreate(
            filial_id=filial_id, vin=VIN, brand="Toyota", starts_at=date.today() - timedelta(days=1000),
            duration_months=1,
        ),
        None,
    )
    assert warranty.status == "vencida"


@pytest.mark.asyncio
async def test_duplicate_vin_in_same_filial_is_rejected(env):
    service, _session, filial_id = env
    await service.create_vehicle_warranty(
        VehicleWarrantyCreate(filial_id=filial_id, vin=VIN, brand="Toyota", starts_at=date.today(), duration_months=12),
        None,
    )
    with pytest.raises(ConflictError):
        await service.create_vehicle_warranty(
            VehicleWarrantyCreate(filial_id=filial_id, vin=VIN, brand="Honda", starts_at=date.today(), duration_months=12),
            None,
        )


@pytest.mark.asyncio
async def test_get_by_vin_and_list_search(env):
    service, _session, filial_id = env
    await service.create_vehicle_warranty(
        VehicleWarrantyCreate(filial_id=filial_id, vin=VIN, brand="Toyota", model="Corolla", starts_at=date.today(), duration_months=12),
        None,
    )
    found = await service.get_vehicle_warranty_by_vin(filial_id, VIN.lower())
    assert found.vin == VIN

    results = await service.list_vehicle_warranties(filial_id, search="corolla")
    assert len(results) == 1
    assert await service.list_vehicle_warranties(filial_id, search="nissan") == []


@pytest.mark.asyncio
async def test_bulk_import_skips_duplicates_against_db_and_within_batch(env):
    service, _session, filial_id = env
    await service.create_vehicle_warranty(
        VehicleWarrantyCreate(filial_id=filial_id, vin=VIN, brand="Toyota", starts_at=date.today(), duration_months=12),
        None,
    )

    items = [
        VehicleWarrantyBulkItem(vin=VIN, brand="Toyota", starts_at=date.today(), duration_months=12),
        VehicleWarrantyBulkItem(vin="2T1BURHE0JC014565", brand="Chevrolet", starts_at=date.today(), duration_months=36),
        VehicleWarrantyBulkItem(vin="2T1BURHE0JC014565", brand="Chevrolet", starts_at=date.today(), duration_months=36),
    ]
    created, skipped = await service.bulk_create_vehicle_warranties(filial_id, items, None)

    assert len(created) == 1
    assert created[0].vin == "2T1BURHE0JC014565"
    assert skipped == [VIN, "2T1BURHE0JC014565"]


@pytest.mark.asyncio
async def test_create_or_renew_from_sale_uses_filial_default_months(env):
    service, session, filial_id = env
    settings = await service.get_labor_settings(filial_id)
    settings.vehicle_warranty_default_months = 24
    session.commit()

    dealership_vehicle_id = uuid.uuid4()
    warranty = await service.create_or_renew_warranty_from_sale(
        filial_id, VIN, "Ford", "Fiesta", date(2026, 1, 1), dealership_vehicle_id
    )

    assert warranty.duration_months == 24
    assert warranty.expires_at == date(2028, 1, 1)
    assert warranty.source.value == "venta"
    assert warranty.dealership_vehicle_id == dealership_vehicle_id


@pytest.mark.asyncio
async def test_create_or_renew_from_sale_restarts_an_existing_warranty(env):
    service, _session, filial_id = env
    await service.create_vehicle_warranty(
        VehicleWarrantyCreate(filial_id=filial_id, vin=VIN, brand="Toyota", starts_at=date(2020, 1, 1), duration_months=12, note="Vieja"),
        None,
    )

    warranty = await service.create_or_renew_warranty_from_sale(
        filial_id, VIN, "Toyota", "Corolla", date(2026, 6, 1), uuid.uuid4()
    )

    assert warranty.starts_at == date(2026, 6, 1)
    assert warranty.source.value == "venta"
    assert warranty.note is None


@pytest.mark.asyncio
async def test_marking_a_dealership_vehicle_sold_auto_creates_the_warranty(env):
    service, session, filial_id = env
    concesionario = ConcesionarioService(service.db)

    dealership_vehicle = DealershipVehicle(
        filial_id=filial_id,
        status=VehicleStatus.DISPONIBLE,
        condition=VehicleCondition.NUEVO,
        brand="Kia",
        model="Sportage",
        year=2026,
        vin=VIN,
        sku="SKU-1",
        price_cash=25000,
        price_financed=27000,
        cost_price=20000,
    )
    session.add(dealership_vehicle)
    session.commit()

    await concesionario.update_vehicle(
        dealership_vehicle.id,
        VehicleUpdate(
            status=VehicleStatus.VENDIDO,
            sale=VehicleSaleInput(client_name="Juan Pérez", sale_type=SaleType.CONTADO, final_price=25000),
        ),
    )

    warranty = session.query(VehicleWarranty).filter_by(filial_id=filial_id, vin=VIN).one()
    assert warranty.source.value == "venta"
    assert warranty.brand == "Kia"
    assert warranty.dealership_vehicle_id == dealership_vehicle.id
    assert warranty.duration_months == 36
