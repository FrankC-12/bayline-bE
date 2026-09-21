"""IGTF taxes the actual payment amount, which already includes IVA — it is
not computed on the pre-tax base alone. DealershipVehicle.igtf_amount used
to apply the 3% only to price_cash, understating IGTF relative to
service_orders/billing.py's (correct) IVA-inclusive base.

igtf_amount is also only an ESTIMATE ("if paid entirely in USD") — it is
never part of cash_total (the vehicle's list price), since the real IGTF
charged depends on how a sale is actually paid, computed at sale time by
ConcesionarioService.update_vehicle (see test_vehicle_sale_igtf.py)."""

import uuid

from app.modules.concesionario.models import DealershipVehicle


def _vehicle(**overrides):
    defaults = dict(
        filial_id=uuid.uuid4(), condition="usado", brand="Toyota", model="Corolla", year=2022,
        vin=str(uuid.uuid4())[:17], sku="SKU-1", price_cash=10_000, price_financed=11_000,
        price_currency="USD", iva_percentage=16, igtf_percentage=3, luxury_tax_percentage=0,
    )
    defaults.update(overrides)
    return DealershipVehicle(**defaults)


def test_igtf_is_computed_on_the_iva_inclusive_base():
    vehicle = _vehicle(price_cash=10_000, iva_percentage=16, igtf_percentage=3)

    assert vehicle.iva_amount == 1_600.0
    # 3% of (10_000 + 1_600) = 348.0, not 3% of 10_000 = 300.0
    assert vehicle.igtf_amount == 348.0


def test_cash_total_excludes_igtf():
    vehicle = _vehicle(price_cash=10_000, iva_percentage=16, igtf_percentage=3, luxury_tax_percentage=0)

    assert vehicle.cash_total == 10_000 + 1_600.0


def test_igtf_is_zero_when_not_paid_in_usd():
    vehicle = _vehicle(price_cash=10_000, price_currency="VES")

    assert vehicle.igtf_amount == 0.0
