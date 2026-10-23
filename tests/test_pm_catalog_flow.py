"""PM regressions: authoritative catalog costs and FIFO display states."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_tempario_pricing_matches_ods import env

from app.modules.post_ventas.models import TemparioPart
from app.modules.post_ventas.schemas import TemparioCreate, TemparioUpdate
from app.modules.warehouse.models import PartLot
from app.modules.warehouse.service import AlmacenService

__all__ = ["env"]


@pytest.mark.asyncio
async def test_catalog_editor_cannot_override_purchase_cost_and_part_filter_is_scoped(env):
    catalog, orders, order, tempario = env
    session = orders.db.session
    existing = session.scalar(select(TemparioPart).where(TemparioPart.tempario_id == tempario.id))
    part_id = existing.part_id
    name, quantity = existing.name, existing.quantity
    before = await catalog.get_tempario(tempario.id)
    await catalog.update_tempario(
        tempario.id,
        TemparioUpdate(
            parts=[{"part_id": part_id, "name": name, "quantity": quantity, "unit_cost": 99999}]
        ),
    )
    after = await catalog.get_tempario(tempario.id)
    stored = session.scalar(select(TemparioPart).where(TemparioPart.tempario_id == tempario.id))
    assert float(stored.unit_cost) != 99999
    assert before.total_with_iva == after.total_with_iva
    assert [item.id for item in await catalog.list_temparios(order.filial_id, part_id=part_id)] == [
        tempario.id
    ]
    assert await catalog.list_temparios(uuid.uuid4(), part_id=part_id) == []


@pytest.mark.asyncio
async def test_fifo_waiting_status_does_not_remove_stock_or_ignore_earlier_lot(env):
    _, orders, order, tempario = env
    session = orders.db.session
    part = session.scalar(select(TemparioPart).where(TemparioPart.tempario_id == tempario.id))
    first = session.scalar(select(PartLot).where(PartLot.part_id == part.part_id))
    first.received_at = datetime.now(UTC) - timedelta(days=1)
    later = PartLot(
        filial_id=order.filial_id,
        warehouse_id=first.warehouse_id,
        part_id=part.part_id,
        quantity_received=10,
        quantity_remaining=10,
        unit_cost=20,
        received_at=datetime.now(UTC),
    )
    session.add(later)
    session.commit()
    service = AlmacenService(orders.db)
    states = await service.lot_fifo_states(order.filial_id, {part.part_id})
    assert states[first.id] == "en_uso" and states[later.id] == "en_espera_fifo"
    assert later.quantity_remaining == 10
    first.quantity_remaining = 0
    session.commit()
    states = await service.lot_fifo_states(order.filial_id, {part.part_id})
    assert states[first.id] == "agotado" and states[later.id] == "en_uso"


@pytest.mark.parametrize("year", [1899, 2101, 2020.5])
def test_open_year_fields_reject_out_of_range_or_fractional_values(year):
    with pytest.raises(ValidationError):
        TemparioUpdate(year_from=year)
    with pytest.raises(ValidationError):
        TemparioCreate(
            filial_id=uuid.uuid4(), category="motor", name="Prueba", estimated_hours=1, year_to=year
        )
