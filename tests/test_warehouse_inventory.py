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
from app.modules.warehouse.exceptions import NoStockAtWarehouseError
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
            id=uuid.uuid4(), category_id=uuid.uuid4(), filial_id=filial_id, code="P1", name="Repuesto",
            price=0, stock_quantity=2,
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


@pytest.mark.asyncio
async def test_inventory_can_be_filtered_by_part_for_the_transfer_modal_hint(env):
    """Powers "disponible en cada almacén" when picking a part on a new
    transfer order — filtering server-side instead of the client fetching
    every lot in the filial just to find one part's rows."""
    service, session, filial_id, warehouse, part = env
    other_part = Part(
        id=uuid.uuid4(), category_id=uuid.uuid4(), filial_id=filial_id, code="P2", name="Otro repuesto", price=0,
    )
    session.add(other_part)
    session.add(
        PartLot(
            filial_id=filial_id, warehouse_id=warehouse.id, part_id=other_part.id,
            quantity_received=5, quantity_remaining=5, unit_cost=1,
        )
    )
    session.commit()

    rows = await service.get_inventory(filial_id, part_id=part.id)

    assert {r.part_id for r in rows} == {part.id}
    assert sum(r.quantity for r in rows) == 2


@pytest.mark.asyncio
async def test_editing_the_location_updates_every_in_stock_lot(env):
    """Not just the newest lot — otherwise the edit would silently
    "revert" once that lot sold out and an older, untouched lot became the
    one left with stock."""
    service, session, filial_id, warehouse, part = env

    row = await service.set_inventory_location(filial_id, part.id, warehouse.id, "Estante A3")
    assert row.location == "Estante A3"

    lots = session.query(PartLot).filter(PartLot.part_id == part.id).all()
    assert all(lot.location == "Estante A3" for lot in lots)


@pytest.mark.asyncio
async def test_location_edit_survives_the_newest_lot_selling_out(env):
    service, session, filial_id, warehouse, part = env
    await service.set_inventory_location(filial_id, part.id, warehouse.id, "Estante A3")

    newest_lot = (
        session.query(PartLot)
        .filter(PartLot.part_id == part.id)
        .order_by(PartLot.received_at.desc())
        .first()
    )
    newest_lot.quantity_remaining = 0
    session.commit()

    rows = await service.get_inventory(filial_id, part_id=part.id)
    assert rows[0].location == "Estante A3"


@pytest.mark.asyncio
async def test_a_later_stock_in_with_its_own_location_still_wins(env):
    service, session, filial_id, warehouse, part = env
    await service.set_inventory_location(filial_id, part.id, warehouse.id, "Estante A3")

    session.add(
        PartLot(
            filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
            quantity_received=1, quantity_remaining=1, unit_cost=15, location="Estante B1",
            received_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    )
    session.commit()

    rows = await service.get_inventory(filial_id, part_id=part.id)
    assert rows[0].location == "Estante B1"


@pytest.mark.asyncio
async def test_clearing_the_location_sets_it_to_none(env):
    service, _session, filial_id, warehouse, part = env
    await service.set_inventory_location(filial_id, part.id, warehouse.id, "Estante A3")

    row = await service.set_inventory_location(filial_id, part.id, warehouse.id, "")
    assert row.location is None


@pytest.mark.asyncio
async def test_editing_the_location_of_a_part_with_no_stock_here_raises(env):
    service, _session, filial_id, warehouse, part = env
    other_warehouse_id = uuid.uuid4()

    with pytest.raises(NoStockAtWarehouseError):
        await service.set_inventory_location(filial_id, part.id, other_warehouse_id, "Estante A3")
