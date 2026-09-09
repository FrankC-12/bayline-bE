"""The inventory dashboard's subtitle promises 'lote FIFO activo' — the
oldest remaining lot's cost, not a blended average across all lots.
get_average_cost() itself must stay untouched: transfers and the P&L
fallback in administracion/service.py still rely on the weighted average."""

import os
import uuid
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.parts.models import Part
from app.modules.warehouse.models import PartLot, Warehouse
from app.modules.warehouse.service import AlmacenService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        warehouse = Warehouse(id=uuid.uuid4(), filial_id=filial_id, name="Almacén 1")
        part = Part(
            id=uuid.uuid4(), filial_id=filial_id, code="P1", name="Repuesto", price=0, stock_quantity=2
        )
        session.add_all([warehouse, part])
        session.add_all(
            [
                PartLot(
                    filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
                    quantity_received=1, quantity_remaining=1, unit_cost=10,
                    received_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
                PartLot(
                    filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
                    quantity_received=1, quantity_remaining=1, unit_cost=20,
                    received_at=datetime(2026, 1, 2, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
        yield AlmacenService(AsyncAdapter(session)), session, filial_id, warehouse, part


@pytest.mark.asyncio
async def test_fifo_unit_cost_is_the_oldest_lot_not_an_average(env):
    service, _session, filial_id, warehouse, part = env

    rows = await service.get_inventory(filial_id)

    row = next(r for r in rows if r.part_id == part.id)
    assert row.fifo_unit_cost == 10.0
    assert await service.get_average_cost(part.id, warehouse.id) == 15.0


@pytest.mark.asyncio
async def test_fifo_unit_cost_stays_the_oldest_lot_after_adding_a_third(env):
    service, session, filial_id, warehouse, part = env
    session.add(
        PartLot(
            filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
            quantity_received=1, quantity_remaining=1, unit_cost=12,
            received_at=datetime(2026, 1, 3, tzinfo=UTC),
        )
    )
    session.commit()

    rows = await service.get_inventory(filial_id)

    row = next(r for r in rows if r.part_id == part.id)
    assert row.fifo_unit_cost == 10.0
    # get_average_cost is untouched — transfers/administracion still see the blend.
    assert await service.get_average_cost(part.id, warehouse.id) == 14.0
