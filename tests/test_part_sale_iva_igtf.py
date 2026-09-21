"""IGTF on a parts counter sale must be computed on (subtotal + IVA), never
on the subtotal alone — the same formula already used for vehicle sales and
service orders. Parts sales previously had no tax layer at all."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.parts.schemas import PartSaleCreate
from app.modules.parts.service import PartsService
from app.modules.post_ventas.models import LaborSettings
from app.modules.warehouse.models import PartLot, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.add(LaborSettings(filial_id=filial_id, hourly_rate=25, iva_percentage=16, igtf_percentage=3))
        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Repuesto", price=0)
        session.add_all([warehouse, part])
        session.commit()
        # "Precio de costo" (1.00 multiplier) keeps the subtotal exactly
        # 45,000.00 with no margin-rounding to account for.
        session.add(
            PartLot(
                filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
                quantity_received=1, quantity_remaining=1, unit_cost=Decimal("45000.00"),
            )
        )
        session.commit()
        db = AsyncAdapter(session)
        yield PartsService(db), filial_id, warehouse.id, part.id


@pytest.mark.asyncio
async def test_tax_breakdown_formula_matches_the_acceptance_example():
    """Directly exercises the formula in isolation: PVP 45,000, IVA 16% ->
    IGTF 1,566.00, total 53,766.00 — computed on (subtotal + IVA), not on
    the subtotal alone (which would give 1,350.00, not 1,566.00)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.add(LaborSettings(filial_id=filial_id, hourly_rate=25, iva_percentage=16, igtf_percentage=3))
        session.commit()
        service = PartsService(AsyncAdapter(session))

        iva_pct, iva_amount, igtf_pct, igtf_amount = await service._tax_breakdown(
            filial_id, Decimal("45000.00")
        )

        assert iva_pct == 16.0
        assert iva_amount == Decimal("7200.00")
        assert igtf_pct == 3.0
        assert igtf_amount == Decimal("1566.00")
        total_with_taxes = Decimal("45000.00") + iva_amount + igtf_amount
        assert total_with_taxes == Decimal("53766.00")
    engine.dispose()


@pytest.mark.asyncio
async def test_quote_sale_includes_the_tax_breakdown(env):
    service, filial_id, warehouse_id, part_id = env
    payload = PartSaleCreate(
        filial_id=filial_id, warehouse_id=warehouse_id, client_name="Cliente",
        discount_label="Precio de costo",
        lines=[dict(part_id=part_id, quantity=1)],
    )
    quote = await service.quote_sale(payload)

    assert quote["total"] == Decimal("45000.00")
    assert quote["iva_amount"] == Decimal("7200.00")
    assert quote["igtf_amount"] == Decimal("1566.00")
    assert quote["total_with_taxes"] == Decimal("53766.00")


@pytest.mark.asyncio
async def test_create_sale_freezes_the_tax_breakdown(env):
    service, filial_id, warehouse_id, part_id = env
    payload = PartSaleCreate(
        filial_id=filial_id, warehouse_id=warehouse_id, client_name="Cliente",
        discount_label="Precio de costo",
        lines=[dict(part_id=part_id, quantity=1)],
    )
    sale = await service.create_sale(payload)

    assert float(sale.iva_percentage) == 16.0
    assert float(sale.iva_amount) == 7200.00
    assert float(sale.igtf_percentage) == 3.0
    assert float(sale.igtf_amount) == 1566.00
    assert sale.total_with_taxes == pytest.approx(53766.00)
