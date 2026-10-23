"""Price changes must be reviewed before inventory or tasks are committed."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from test_part_sales_fifo import inventory
from test_tempario_pricing_matches_ods import env

from app.core.exceptions import ConflictError
from app.modules.parts.models import PartSale
from app.modules.parts.schemas import PartSaleCreate
from app.modules.post_ventas.models import LaborSettings, TemparioPart
from app.modules.post_ventas.schemas import TemparioPricingInput
from app.modules.service_orders.models import ServiceOrderTask

__all__ = ["env", "inventory"]


@pytest.mark.asyncio
async def test_counter_sale_refuses_changed_price_without_consuming_stock(inventory):
    service, session, data, lots, _ = inventory
    payload = PartSaleCreate(**data)
    quote = await service.quote_sale(payload)
    lots[0].unit_cost = Decimal("12.70")
    session.commit()
    confirmed = payload.model_copy(update={"quote_hash": quote["quote_hash"]})
    with pytest.raises(ConflictError) as caught:
        await service.create_sale(confirmed)
    assert caught.value.error_code == "sale_quote_changed"
    assert [lot.quantity_remaining for lot in lots] == [10, 10, 10]
    assert session.scalar(select(func.count()).select_from(PartSale)) == 0
    revised = await service.quote_sale(payload)
    sale = await service.create_sale(
        payload.model_copy(update={"quote_hash": revised["quote_hash"]})
    )
    assert sale.total == float(revised["total"])


@pytest.mark.asyncio
async def test_ods_quote_uses_frozen_parameters_and_blocks_price_change(env):
    catalog, orders, order, tempario = env
    session = orders.db.session
    order.settings_snapshot = {"hourly_rate": 12.70, "iva_percentage": 8}
    order.discount_label = "Precio de costo"
    settings = session.scalar(
        select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
    )
    settings.hourly_rate = 99
    part = session.scalar(select(TemparioPart).where(TemparioPart.tempario_id == tempario.id))
    payload = TemparioPricingInput(
        filial_id=order.filial_id,
        estimated_hours=1,
        parts=[{"part_id": part.part_id, "name": part.name, "quantity": 3, "unit_cost": 999}],
    )
    session.commit()
    quote = await catalog.preview_tempario(payload, order=order)
    assert quote["hourly_rate"] == 12.70
    assert quote["iva_percentage"] == 8
    assert quote["parts_margin"] == 0
    with pytest.raises(ConflictError) as caught:
        await orders.add_task(order.id, tempario.id, expected_total=quote["total_with_iva"] + 1)
    assert caught.value.error_code == "task_quote_changed"
    assert session.scalar(select(func.count()).select_from(ServiceOrderTask)) == 0
    await orders.add_task(order.id, tempario.id, expected_total=quote["total_with_iva"])
    summary = await orders.get_order_summary(order.id)
    assert summary.total == quote["total_with_iva"]
