"""Catalog previews and new ODS use the same FIFO and cent rounding."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, select
from test_tempario_pricing_matches_ods import env

from app.modules.parts.models import Part
from app.modules.post_ventas.models import LaborSettings, TemparioPart
from app.modules.service_orders.enums import TaskStatus
from app.modules.warehouse.models import PartLot

__all__ = ["env"]


@pytest.mark.asyncio
async def test_catalog_ignores_stale_manual_cost_and_uses_weighted_fifo(env):
    catalog, orders, order, tempario = env
    session = orders.db.session
    part = session.scalar(select(TemparioPart).where(TemparioPart.tempario_id == tempario.id))
    part.unit_cost = 999
    oldest = session.scalar(select(PartLot).where(PartLot.part_id == part.part_id))
    oldest.quantity_remaining = 1
    oldest.received_at = datetime.now(UTC) - timedelta(days=2)
    session.add(
        PartLot(
            filial_id=order.filial_id,
            warehouse_id=oldest.warehouse_id,
            part_id=part.part_id,
            quantity_received=2,
            quantity_remaining=2,
            unit_cost=10,
            received_at=datetime.now(UTC),
        )
    )
    session.commit()

    preview = await catalog.get_tempario(tempario.id)
    assert preview.parts_cost == 44
    assert preview.parts_margin == 13.2
    await orders.add_task(order.id, tempario.id)
    summary = await orders.get_order_summary(order.id)
    assert summary.total == preview.total_with_iva == 95.35


@pytest.mark.asyncio
async def test_two_part_lines_round_individually_like_the_ods(env):
    catalog, orders, order, tempario = env
    session = orders.db.session
    first = session.scalar(select(TemparioPart).where(TemparioPart.tempario_id == tempario.id))
    first.quantity = 1
    lot = session.scalar(select(PartLot).where(PartLot.part_id == first.part_id))
    lot.unit_cost = Decimal("0.05")
    other = Part(
        filial_id=order.filial_id, category_id=uuid.uuid4(), code="ROUND", name="Segundo", price=0
    )
    session.add(other)
    session.flush()
    session.add(
        TemparioPart(
            tempario_id=tempario.id, part_id=other.id, name=other.name, quantity=1, unit_cost=999
        )
    )
    session.add(
        PartLot(
            filial_id=order.filial_id,
            warehouse_id=lot.warehouse_id,
            part_id=other.id,
            quantity_received=1,
            quantity_remaining=1,
            unit_cost=Decimal("0.05"),
        )
    )
    session.commit()
    preview = await catalog.get_tempario(tempario.id)
    await orders.add_task(order.id, tempario.id)
    summary = await orders.get_order_summary(order.id)
    assert summary.parts_subtotal == 0.14
    assert preview.parts_cost == 0.10
    assert preview.parts_margin == 0.04
    assert preview.total_with_iva == summary.total


@pytest.mark.asyncio
async def test_12_70_total_is_identical_in_catalog_and_order(env):
    catalog, orders, order, tempario = env
    session = orders.db.session
    tempario.parts.clear()
    tempario.requires_parts = False
    tempario.estimated_hours = Decimal("0.5")
    settings = session.scalar(
        select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
    )
    settings.hourly_rate = Decimal("21.90")
    session.commit()
    preview = await catalog.get_tempario(tempario.id)
    task = await orders.add_task(order.id, tempario.id)
    summary = await orders.get_order_summary(order.id)
    assert preview.total_with_iva == summary.total == 12.70
    assert summary.labor_subtotal == 10.95
    assert summary.iva_amount == 1.75
    await orders.update_task_status(task.id, TaskStatus.CANCELADA)
    cancelled = await orders.get_order_summary(order.id)
    assert cancelled.labor_subtotal == 0
    assert cancelled.technician_commission == 0


@pytest.mark.asyncio
async def test_no_purchase_history_keeps_catalog_estimate_when_added_and_resized(env):
    catalog, orders, order, tempario = env
    session = orders.db.session
    session.execute(delete(PartLot))
    session.commit()
    preview = await catalog.get_tempario(tempario.id)
    assert preview.parts_cost == 72
    assert preview.pricing_notes
    await orders.add_task(order.id, tempario.id, expected_total=preview.total_with_iva)
    summary = await orders.get_order_summary(order.id)
    assert summary.total == preview.total_with_iva
    request = (await orders.list_transfers(order.id))[0]
    await orders.set_transfer_line_quantity(request.lines[0].id, 2)
    resized = await orders.get_order_summary(order.id)
    assert resized.parts_subtotal == 62.4


@pytest.mark.asyncio
async def test_labor_rounds_per_task_like_individual_catalog_services(env):
    catalog, orders, order, tempario = env
    session = orders.db.session
    tempario.parts.clear()
    tempario.requires_parts = False
    tempario.estimated_hours = Decimal("0.10")
    settings = session.scalar(
        select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
    )
    settings.hourly_rate = Decimal("25.35")
    session.commit()
    preview = await catalog.get_tempario(tempario.id)
    assert preview.labor_cost == 2.54
    await orders.add_task(order.id, tempario.id)
    await orders.add_task(order.id, tempario.id)
    summary = await orders.get_order_summary(order.id)
    assert summary.labor_subtotal == 5.08
