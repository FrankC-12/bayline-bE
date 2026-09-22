"""Counter parts sales (Venta de Repuestos) must be visible to almacén
staff alongside Órdenes de Servicio requests — same idea, different
destination (mostrador vs. taller) — since today the almacén screen only
ever surfaced ODS/taller requests, leaving counter-sale dispatches
invisible there."""

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
from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.schemas import PartSaleCreate
from app.modules.parts.service import PartsService
from app.modules.post_ventas.models import LaborSettings
from app.modules.warehouse.models import PartLot, Warehouse
from app.modules.warehouse.service import AlmacenService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.add(LaborSettings(filial_id=filial_id, hourly_rate=25, iva_percentage=16))
        warehouse = Warehouse(filial_id=filial_id, name="Mostrador")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Bujía", price=0)
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
        yield PartsService(db), AlmacenService(db), filial_id, warehouse, part.id


def _payload(filial_id, warehouse_id, part_id):
    return PartSaleCreate(
        filial_id=filial_id, warehouse_id=warehouse_id, client_name="Cliente Mostrador",
        discount_label="Precio de costo",
        lines=[dict(part_id=part_id, quantity=1)],
    )


@pytest.mark.asyncio
async def test_a_created_sale_is_listed_as_a_request(env):
    parts_service, almacen_service, filial_id, warehouse, part_id = env
    sale = await parts_service.create_sale(_payload(filial_id, warehouse.id, part_id))

    results = await almacen_service.list_part_sale_requests(filial_id)

    assert len(results) == 1
    assert results[0].id == sale.id
    assert results[0].client_name == "Cliente Mostrador"
    assert results[0].status == "pendiente"
    assert results[0].lines[0].warehouse_name == "Mostrador"


@pytest.mark.asyncio
async def test_a_cancelled_sale_is_excluded(env):
    parts_service, almacen_service, filial_id, warehouse, part_id = env
    sale = await parts_service.create_sale(_payload(filial_id, warehouse.id, part_id))
    await parts_service.update_sale_status(sale.id, PartSaleStatus.CANCELADO)

    assert await almacen_service.list_part_sale_requests(filial_id) == []


@pytest.mark.asyncio
async def test_scoped_to_filial(env):
    parts_service, almacen_service, filial_id, warehouse, part_id = env
    await parts_service.create_sale(_payload(filial_id, warehouse.id, part_id))

    assert await almacen_service.list_part_sale_requests(uuid.uuid4()) == []
