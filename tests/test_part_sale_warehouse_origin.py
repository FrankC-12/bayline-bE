"""A counter parts sale (unlike an ODT) is always scoped to a single
warehouse, chosen once at creation — the sale must expose which one, so
staff can tell it apart from a taller/ODS request drawing from wherever
FIFO lands, and know exactly where to pull the parts from."""

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
        session.add(LaborSettings(filial_id=filial_id, hourly_rate=25, iva_percentage=16))
        warehouse = Warehouse(filial_id=filial_id, name="Almacén Repuestos")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Repuesto", price=0)
        session.add_all([warehouse, part])
        session.commit()
        session.add(
            PartLot(
                filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
                quantity_received=5, quantity_remaining=5, unit_cost=Decimal("10.00"),
            )
        )
        session.commit()
        db = AsyncAdapter(session)
        yield PartsService(db), filial_id, warehouse, part.id


@pytest.mark.asyncio
async def test_created_sale_reports_its_warehouse(env):
    service, filial_id, warehouse, part_id = env
    payload = PartSaleCreate(
        filial_id=filial_id, warehouse_id=warehouse.id, client_name="Cliente",
        discount_label="Precio de costo",
        lines=[dict(part_id=part_id, quantity=1)],
    )

    sale = await service.create_sale(payload)

    assert sale.warehouse_id == warehouse.id
    assert sale.warehouse_name == "Almacén Repuestos"


@pytest.mark.asyncio
async def test_listed_sale_still_reports_its_warehouse(env):
    service, filial_id, warehouse, part_id = env
    payload = PartSaleCreate(
        filial_id=filial_id, warehouse_id=warehouse.id, client_name="Cliente",
        discount_label="Precio de costo",
        lines=[dict(part_id=part_id, quantity=1)],
    )
    await service.create_sale(payload)

    sales = await service.list_sales(filial_id)

    assert sales[0].warehouse_name == "Almacén Repuestos"
