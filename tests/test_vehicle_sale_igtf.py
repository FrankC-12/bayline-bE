"""IGTF is not part of a vehicle's list price (DealershipVehicle.cash_total
is base + IVA + luxury tax only) — it's computed at sale time, on whatever
portion of the payment actually lands in foreign currency, the same
calculate_payment() used by service_orders/billing.py. A sale paid entirely
in bolívares owes no IGTF at all, even though the vehicle is priced in USD."""

import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.modules.auth.schemas import CurrentUser
from app.modules.concesionario.enums import SaleType, VehicleCondition, VehicleStatus
from app.modules.concesionario.models import DealershipVehicle, VehicleSale
from app.modules.concesionario.schemas import VehicleSaleInput, VehicleUpdate
from app.modules.concesionario.service import ConcesionarioService
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.filiales.models import Filial
from app.modules.roles.enums import RoleScope
from app.modules.service_orders.billing import billing_day

_CALLER = CurrentUser(
    user_id=uuid.uuid4(), email="vendedor@test.com", role_id=uuid.uuid4(), role_slug="vendedor",
    scope=RoleScope.FILIAL, holding_id=None, filial_id=uuid.uuid4(),
)


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Concesionario", slug="conce"))
        session.commit()
        vehicle = DealershipVehicle(
            filial_id=filial_id, status=VehicleStatus.DISPONIBLE, condition=VehicleCondition.NUEVO,
            brand="Toyota", model="Corolla", year=2026, vin=str(uuid.uuid4())[:17], sku="SKU-1",
            price_cash=10_000, price_financed=11_000, cost_price=8_000,
            price_currency="USD", iva_percentage=16, igtf_percentage=3,
        )
        session.add(vehicle)
        session.commit()
        db = AsyncAdapter(session)
        yield ConcesionarioService(db), session, filial_id, vehicle


def _sell(service, vehicle_id, **sale_kwargs):
    return service.update_vehicle(
        vehicle_id,
        VehicleUpdate(
            status=VehicleStatus.VENDIDO,
            sale=VehicleSaleInput(client_name="Cliente de Prueba", sale_type=SaleType.CONTADO, **sale_kwargs),
        ),
        _CALLER,
    )


@pytest.mark.asyncio
async def test_full_usd_payment_charges_igtf_on_price_plus_iva(env):
    service, session, filial_id, vehicle = env

    updated = await _sell(service, vehicle.id, payment_method="usd")

    sale = session.query(VehicleSale).filter_by(vehicle_id=vehicle.id).one()
    # base = 10_000 + 16% IVA = 11_600; IGTF = 3% of 11_600 = 348.
    assert sale.usd_base == 11_600.0
    assert sale.igtf_amount == 348.0
    assert sale.final_price == 11_948.0
    assert updated.status == VehicleStatus.VENDIDO


@pytest.mark.asyncio
async def test_full_bolivar_payment_owes_no_igtf(env):
    service, session, filial_id, vehicle = env
    session.add(ExchangeRate(currency="USD", rate_ves=40, value_date=billing_day()))
    session.commit()

    await _sell(service, vehicle.id, payment_method="bs")

    sale = session.query(VehicleSale).filter_by(vehicle_id=vehicle.id).one()
    assert sale.usd_base == 0.0
    assert sale.igtf_amount == 0.0
    # No USD lands anywhere, so the total owed stays the plain base+IVA.
    assert sale.final_price == 11_600.0


@pytest.mark.asyncio
async def test_mixed_payment_charges_igtf_only_on_the_usd_portion(env):
    service, session, filial_id, vehicle = env
    session.add(ExchangeRate(currency="USD", rate_ves=40, value_date=billing_day()))
    session.commit()

    await _sell(service, vehicle.id, payment_method="mixed", usd_base=5_000)

    sale = session.query(VehicleSale).filter_by(vehicle_id=vehicle.id).one()
    assert sale.usd_base == 5_000.0
    assert sale.igtf_amount == 150.0  # 3% of 5_000
    assert sale.final_price == 11_600.0 + 150.0
    assert sale.bcv_rate == 40.0


@pytest.mark.asyncio
async def test_bolivar_payment_without_a_bcv_rate_is_rejected(env):
    service, session, filial_id, vehicle = env

    with pytest.raises(Exception, match="tasa BCV"):
        await _sell(service, vehicle.id, payment_method="bs")


@pytest.mark.asyncio
async def test_financed_sale_never_touches_igtf(env):
    service, session, filial_id, vehicle = env

    await service.update_vehicle(
        vehicle.id,
        VehicleUpdate(
            status=VehicleStatus.VENDIDO,
            sale=VehicleSaleInput(client_name="Cliente Financiado", sale_type=SaleType.FINANCIADO),
        ),
        _CALLER,
    )

    sale = session.query(VehicleSale).filter_by(vehicle_id=vehicle.id).one()
    assert sale.payment_method is None
    assert sale.igtf_amount == 0.0
    assert sale.final_price == 11_000.0  # price_financed, untouched


def test_contado_without_payment_method_is_rejected_by_the_schema():
    with pytest.raises(ValidationError):
        VehicleSaleInput(client_name="Cliente de Prueba", sale_type=SaleType.CONTADO)


@pytest.mark.asyncio
async def test_dealership_vehicle_list_price_excludes_igtf(env):
    """The 'ficha del vehículo' must never show IGTF baked into the total —
    it's base + IVA (+ luxury tax) only, IGTF only shows up once a sale
    actually happens."""
    _service, _session, _filial_id, vehicle = env

    assert vehicle.cash_total == 11_600.0
