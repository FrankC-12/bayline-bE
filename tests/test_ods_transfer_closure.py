"""Delivery closes all child requests without inventing dispatch or pickup."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import test_part_sales_fifo as inventory_fixtures
import test_parts_discounts as order_fixtures
from billing_support import configure_billing, invoice_payload
from sqlalchemy import func, select

from app.core.exceptions import BadRequestError, ConflictError
from app.modules.service_orders.enums import ServiceOrderStatus, TransferStatus
from app.modules.service_orders.models import ServiceOrderTransfer
from app.modules.warehouse.enums import TransferStatus as WarehouseTransferStatus
from app.modules.warehouse.models import PartLot, StockMovement, Transfer, Warehouse
from app.modules.warehouse.reservations import reserved_by_lot

inventory = inventory_fixtures.inventory
order_inventory = order_fixtures.order_inventory


@pytest.mark.asyncio
async def test_delivery_closes_all_odts_without_consuming_stock_and_keeps_receipt_history(
    order_inventory,
):
    service, session, order, part_id, _lots = order_inventory
    billing, accounts, _ = configure_billing(service, session, order, igtf=0)
    pending = await service.add_transfer_line(order.id, part_id, 1)
    waiting = await service._get_or_create_pending_transfer(order.id, force_new=True)
    await service.add_transfer_line(order.id, part_id, 1, transfer=waiting)
    waiting.status = TransferStatus.PEDIDO
    waiting.fulfilled_at = datetime.now(UTC) - timedelta(hours=2)
    received = await service._get_or_create_pending_transfer(order.id, force_new=True)
    await service.add_transfer_line(order.id, part_id, 1, transfer=received)
    received.status = TransferStatus.COMPLETADO
    received.stock_deducted = True
    received.completed_at = datetime.now(UTC) - timedelta(hours=1)
    received.picked_up_at = received.completed_at
    received.pickup_photo_url = "/api/v1/private/receipt.jpg"
    receipt_at = received.completed_at
    empty = await service._get_or_create_pending_transfer(order.id, force_new=True)
    session.commit()
    assert await reserved_by_lot(service.db, part_id)
    warehouses = session.scalars(
        select(Warehouse).where(Warehouse.filial_id == order.filial_id)
    ).all()
    relocation = Transfer(
        filial_id=order.filial_id,
        sequence_number=1,
        origin_warehouse_id=warehouses[0].id,
        destination_warehouse_id=warehouses[1].id,
        workshop_request_id=waiting.id,
        status=WarehouseTransferStatus.EN_PROCESO,
        note="Repuesto pendiente",
    )
    historical = Transfer(
        filial_id=order.filial_id,
        sequence_number=2,
        origin_warehouse_id=warehouses[0].id,
        destination_warehouse_id=warehouses[1].id,
        workshop_request_id=received.id,
        status=WarehouseTransferStatus.COMPLETADA,
    )
    session.add_all([relocation, historical])
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()
    await billing.issue(order.id, await invoice_payload(billing, order, accounts), None)
    stock_before = list(session.execute(select(PartLot.id, PartLot.quantity_remaining)).all())
    movements_before = session.scalar(select(func.count()).select_from(StockMovement))
    actor = uuid.uuid4()
    closed = await service.close_order(order.id, closed_by_user_id=actor)
    assert closed.status == ServiceOrderStatus.ORDEN_CERRADA
    for request in [pending, waiting, received, empty]:
        assert request.closed_at.replace(tzinfo=None) == closed.closed_at.replace(tzinfo=None)
        assert request.closed_by_user_id == actor
    assert pending.stock_deducted is False and waiting.stock_deducted is False
    assert pending.picked_up_at is None and waiting.picked_up_at is None
    assert received.completed_at.replace(tzinfo=None) == receipt_at.replace(tzinfo=None)
    assert received.picked_up_at.replace(tzinfo=None) == receipt_at.replace(tzinfo=None)
    assert received.pickup_photo_url == "/api/v1/private/receipt.jpg"
    assert relocation.status == WarehouseTransferStatus.CANCELADA
    assert "Repuesto pendiente" in relocation.note and order.code in relocation.note
    assert historical.status == WarehouseTransferStatus.COMPLETADA
    assert await reserved_by_lot(service.db, part_id) == {}
    assert (
        list(session.execute(select(PartLot.id, PartLot.quantity_remaining)).all()) == stock_before
    )
    assert session.scalar(select(func.count()).select_from(StockMovement)) == movements_before
    summary = await service.get_order_summary(order.id)
    assert summary.transfers and all(t.closed_at for t in summary.transfers)
    from app.modules.service_orders.dispatch import WorkshopDispatchService

    with pytest.raises(ConflictError) as err:
        await WorkshopDispatchService(service.db).dispatch(waiting.id)
    assert err.value.error_code == "transfer_closed"


@pytest.mark.asyncio
async def test_rejected_delivery_does_not_close_child_requests(order_inventory):
    service, session, order, part_id, _ = order_inventory
    request = await service.add_transfer_line(order.id, part_id, 1)
    with pytest.raises(BadRequestError):
        await service.close_order(order.id)
    assert session.get(ServiceOrderTransfer, request.id).closed_at is None
    assert order.status != ServiceOrderStatus.ORDEN_CERRADA
