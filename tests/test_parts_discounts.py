"""Parts-only discount persistence and regression coverage."""

import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError
from test_part_sales_fifo import inventory as fifo_inventory

from app.modules.parts.pricing import PARTS_MULTIPLIERS
from app.modules.parts.schemas import PartSaleCreate
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask, ServiceOrderTransfer
from app.modules.service_orders.schemas import ServiceOrderUpdate
from app.modules.service_orders.service import ServiceOrderService

inventory = fifo_inventory

LEVELS = list(zip(PARTS_MULTIPLIERS, [15.60, 14.40, 13.20, 12.00], strict=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("label,unit_price", LEVELS)
async def test_counter_sale_all_discount_levels(inventory, label, unit_price):
    service, session, data, lots, _ = inventory
    lots[0].unit_cost = 12
    session.commit()
    data["lines"][0]["quantity"] = 10
    data["discount_label"] = label
    payload = PartSaleCreate(**data)
    quote = await service.quote_sale(payload)
    sale = await service.create_sale(payload)
    session.expire_all()
    sale = await service.get_sale(sale.id)
    assert sale.discount_label == label
    assert sale.total == round(unit_price * 10, 2)
    assert float(quote["total"]) == sale.total
    assert sale.lines[0].unit_cost == 12


@pytest.fixture
def order_inventory(inventory):
    parts, session, data, lots, _ = inventory
    # ODS snapshots the current catalog cost; changing margins must never read it again.
    lots[-1].unit_cost = 12
    order = ServiceOrder(
        id=uuid.uuid4(), filial_id=data["filial_id"], vehicle_id=uuid.uuid4(), sequence_number=2001
    )
    session.add(order)
    session.commit()
    return ServiceOrderService(parts.db), session, order, data["lines"][0]["part_id"], lots


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ServiceOrderStatus.PENDIENTE, ServiceOrderStatus.COMPLETADO])
@pytest.mark.parametrize("labor_hours", [0, 2])
async def test_ods_discount_persists_without_compounding_or_changing_labor(
    order_inventory, status, labor_hours
):
    service, session, order, part_id, lots = order_inventory
    await service.add_transfer_line(order.id, part_id, 1)
    if labor_hours:
        session.add(
            ServiceOrderTask(
                service_order_id=order.id,
                tempario_id=uuid.uuid4(),
                code_snapshot="MO",
                name_snapshot="Trabajo",
                hours_snapshot=labor_hours,
            )
        )
    order.status = status
    if status == ServiceOrderStatus.ORDEN_CERRADA:
        order.total_amount = Decimal("18.10")
    session.commit()
    lots[-1].unit_cost = 99
    session.commit()
    for label, expected in [*LEVELS, LEVELS[0]]:
        await service.update_order(order.id, ServiceOrderUpdate(discount_label=label))
        session.expire_all()
        summary = await service.get_order_summary(order.id)
        assert summary.discount_label == label
        assert summary.parts_subtotal == expected
        assert summary.labor_subtotal == labor_hours * 25
        # Existing IVA calculation is unchanged, including its rounding/display contract.
        assert summary.iva_amount == (expected + labor_hours * 25) * 16 / 100
        assert round(summary.total, 2) == round((expected + labor_hours * 25) * 1.16, 2)
        assert summary.transfers[0].lines[0].unit_price == expected
        assert summary.transfers[0].subtotal == expected
        if status == ServiceOrderStatus.ORDEN_CERRADA:
            assert (await service.get_order(order.id)).total_amount == Decimal("18.10")


@pytest.mark.asyncio
async def test_one_order_discount_applies_to_every_odt_and_future_parts(order_inventory):
    service, session, order, part_id, _ = order_inventory
    await service.add_transfer_line(order.id, part_id, 1)
    second = ServiceOrderTransfer(service_order_id=order.id, sequence_number=2)
    session.add(second)
    session.flush()
    await service._add_line_to_transfer(second, part_id, 2, 12)
    session.commit()
    label = "Costo + 20% (Descuento 10%)"
    await service.update_order(order.id, ServiceOrderUpdate(discount_label=label))
    summary = await service.get_order_summary(order.id)
    assert summary.parts_subtotal == pytest.approx(43.20)
    assert [tr.subtotal for tr in summary.transfers] == [14.4, 28.8]
    await service.add_transfer_line(order.id, part_id, 1)
    summary = await service.get_order_summary(order.id)
    assert summary.parts_subtotal == pytest.approx(57.60)
    assert all(line.unit_price == 14.4 for tr in summary.transfers for line in tr.lines)


@pytest.mark.asyncio
async def test_margin_cannot_be_combined_with_closing(order_inventory):
    from app.core.exceptions import BadRequestError

    service, session, order, part_id, _ = order_inventory
    await service.add_transfer_line(order.id, part_id, 1)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()
    with pytest.raises(BadRequestError):
        await service.update_order(
            order.id,
            ServiceOrderUpdate(
                discount_label="Precio de costo", status=ServiceOrderStatus.ORDEN_CERRADA
            ),
        )
    assert order.status == ServiceOrderStatus.COMPLETADO
    assert order.invoiced_at is None


def test_unknown_discount_is_rejected(inventory):
    _, _, data, _, _ = inventory
    with pytest.raises(ValidationError):
        PartSaleCreate(**data, discount_label="50%")
    with pytest.raises(ValidationError):
        ServiceOrderUpdate(discount_label="50%")


@pytest.mark.asyncio
async def test_ods_http_discount_update_and_summary(order_inventory, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.modules.auth.dependencies import get_current_user
    from app.modules.service_orders import router as routes

    service, _, order, part_id, _ = order_inventory
    await service.add_transfer_line(order.id, part_id, 1)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: object()

    async def allowed(*args, **kwargs):
        pass

    monkeypatch.setattr(routes, "_ensure_access", allowed)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for label, expected in LEVELS:
            response = await client.patch(
                f"/api/v1/service-orders/{order.id}", json={"discount_label": label}
            )
            assert response.status_code == 200
            assert response.json()["discount_label"] == label
            summary = await client.get(f"/api/v1/service-orders/{order.id}/summary")
            assert summary.status_code == 200
            assert summary.json()["parts_subtotal"] == expected
            assert summary.json()["discount_label"] == label
        invalid = await client.patch(
            f"/api/v1/service-orders/{order.id}", json={"discount_label": "50%"}
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,total", list(zip(PARTS_MULTIPLIERS, [260, 240, 220, 200], strict=True))
)
async def test_discount_preserves_fifo_allocation_and_exact_totals(inventory, label, total):
    service, _, data, _, _ = inventory
    data["lines"][0]["quantity"] = 15
    sale = await service.create_sale(PartSaleCreate(**data, discount_label=label))
    assert sale.total == total
    line = sale.lines[0]
    assert round(line.unit_price * 15, 2) == line.line_total
    assert [(allocation.quantity, allocation.unit_cost) for allocation in line.allocations] == [
        (10, 10),
        (5, 20),
    ]
