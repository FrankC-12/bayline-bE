"""ODS lines remain readable independently of active catalog filters."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from test_tempario_pricing_matches_ods import env

from app.modules.parts.models import Part
from app.modules.service_orders.enums import ServiceOrderStatus

__all__ = ["env"]


@pytest.mark.asyncio
async def test_regular_order_returns_part_labels_even_when_part_is_inactive(env):
    _, service, order, tempario = env
    await service.add_task(order.id, tempario.id)
    before = await service.get_order_summary(order.id)
    part = service.db.session.scalar(select(Part))
    part.is_active = False
    service.db.session.commit()
    after = await service.get_order_summary(order.id)
    line = after.transfers[0].lines[0]
    assert line.part_name == "Filtro" and line.part_code == "P-1"
    assert after.total == before.total
    assert line.subtotal == before.transfers[0].lines[0].subtotal


@pytest.mark.asyncio
async def test_old_frozen_invoice_gains_labels_without_mutating_its_financial_snapshot(env):
    _, service, order, tempario = env
    await service.add_task(order.id, tempario.id)
    summary = await service.get_order_summary(order.id)
    snapshot = summary.model_dump(mode="json")
    for transfer in snapshot["transfers"]:
        for line in transfer["lines"]:
            line.pop("part_name")
            line.pop("part_code")
    order.pricing_snapshot = deepcopy(snapshot)
    order.invoiced_at = datetime.now(UTC)
    service.db.session.commit()
    result = await service.get_order_summary(order.id)
    assert result.transfers[0].lines[0].part_name == "Filtro"
    assert result.total == summary.total
    assert order.pricing_snapshot == snapshot


@pytest.mark.asyncio
async def test_closed_legacy_order_shows_labels_without_inventing_historical_prices(env):
    _, service, order, tempario = env
    await service.add_task(order.id, tempario.id)
    order.status = ServiceOrderStatus.ORDEN_CERRADA
    order.pricing_snapshot = None
    service.db.session.commit()
    result = await service.get_order_summary(order.id)
    line = result.transfers[0].lines[0]
    assert line.part_name == "Filtro" and line.part_code == "P-1"
    assert line.unit_price is None and line.subtotal is None
