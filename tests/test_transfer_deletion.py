"""Deletion releases reservations, never silently reverses physical inventory."""

import uuid

import pytest
import test_service_order_transfer_completado as transfer_fixtures
from sqlalchemy import select

from app.core.exceptions import ConflictError
from app.modules.service_orders.dispatch import WorkshopDispatchService
from app.modules.service_orders.exceptions import TransferNotFoundError
from app.modules.service_orders.models import (
    ServiceOrderTransfer,
    ServiceOrderTransferLotAllocation,
)
from app.modules.warehouse.enums import TransferStatus
from app.modules.warehouse.models import PartLot, Transfer, TransferLine
from app.modules.warehouse.service import AlmacenService


@pytest.fixture
def deletion_env():
    yield from transfer_fixtures.env.__wrapped__()


@pytest.mark.asyncio
async def test_delete_pending_request_without_consuming_stock(deletion_env):
    service, session, order, part = deletion_env
    request = await service.add_transfer_line(order.id, part.id, 2)
    await WorkshopDispatchService(service.db).delete(request.id, order_id=order.id)
    assert session.get(ServiceOrderTransfer, request.id) is None
    assert not session.scalars(select(ServiceOrderTransferLotAllocation)).all()
    assert session.scalars(select(PartLot.quantity_remaining)).all() == [5]


@pytest.mark.asyncio
async def test_delete_ordered_request_is_rejected_and_keeps_reservations(deletion_env):
    service, session, order, part = deletion_env
    request = await service.add_transfer_line(order.id, part.id, 2)
    request_id = request.id
    await service.mark_transfer_ordered(request_id)
    before = session.scalars(select(ServiceOrderTransferLotAllocation)).all()
    allocation_ids = {allocation.id for allocation in before}
    with pytest.raises(ConflictError, match="pendientes"):
        await WorkshopDispatchService(service.db).delete(request_id)
    assert session.get(ServiceOrderTransfer, request_id) is not None
    assert {
        allocation.id
        for allocation in session.scalars(select(ServiceOrderTransferLotAllocation)).all()
    } == allocation_ids
    assert session.scalars(select(PartLot.quantity_remaining)).all() == [5]


@pytest.mark.asyncio
async def test_delete_rejects_wrong_order_and_dispatched_request(deletion_env):
    service, session, order, part = deletion_env
    request = await service.add_transfer_line(order.id, part.id, 2)
    request_id = request.id
    with pytest.raises(TransferNotFoundError):
        await WorkshopDispatchService(service.db).delete(request_id, order_id=uuid.uuid4())
    await service.mark_transfer_ordered(request_id)
    await service.complete_transfer(request_id)
    with pytest.raises(ConflictError, match="despachada"):
        await WorkshopDispatchService(service.db).delete(request_id)
    assert session.get(ServiceOrderTransfer, request_id) is not None
    assert session.scalars(select(PartLot.quantity_remaining)).all() == [3]


@pytest.mark.asyncio
async def test_delete_request_removes_pending_transfers_retains_received_history(deletion_env):
    service, session, order, part = deletion_env
    request = await service.add_transfer_line(order.id, part.id, 2)
    lot = session.scalars(select(PartLot)).one()
    pending = Transfer(
        filial_id=order.filial_id,
        sequence_number=1,
        origin_warehouse_id=lot.warehouse_id,
        destination_warehouse_id=lot.warehouse_id,
        workshop_request_id=request.id,
        status=TransferStatus.PEDIDO,
    )
    received = Transfer(
        filial_id=order.filial_id,
        sequence_number=2,
        origin_warehouse_id=lot.warehouse_id,
        destination_warehouse_id=lot.warehouse_id,
        workshop_request_id=request.id,
        status=TransferStatus.COMPLETADA,
    )
    received.lines = [
        TransferLine(part_id=part.id, quantity=2, workshop_request_line_id=request.lines[0].id)
    ]
    session.add_all([pending, received])
    session.commit()
    pending_id, received_id = pending.id, received.id
    await WorkshopDispatchService(service.db).delete(request.id)
    assert session.get(Transfer, pending_id) is None
    kept = session.get(Transfer, received_id)
    assert kept.workshop_request_id is None
    assert kept.lines[0].workshop_request_line_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state,linked,allowed",
    [
        (TransferStatus.PEDIDO, False, True),
        (TransferStatus.EN_PROCESO, False, True),
        (TransferStatus.CANCELADA, False, True),
        (TransferStatus.COMPLETADA, False, False),
        (TransferStatus.PEDIDO, True, False),
    ],
)
async def test_general_transfer_deletion_rules(deletion_env, state, linked, allowed):
    service, session, order, part = deletion_env
    request = await service.add_transfer_line(order.id, part.id, 2)
    lot = session.scalars(select(PartLot)).one()
    transfer = Transfer(
        filial_id=order.filial_id,
        sequence_number=1,
        origin_warehouse_id=lot.warehouse_id,
        destination_warehouse_id=lot.warehouse_id,
        status=state,
        workshop_request_id=request.id if linked else None,
    )
    transfer.lines = [TransferLine(part_id=part.id, quantity=2)]
    session.add(transfer)
    session.commit()
    transfer_id = transfer.id
    warehouse = AlmacenService(service.db)
    if allowed:
        await warehouse.delete_transfer(transfer_id)
        assert session.get(Transfer, transfer_id) is None
    else:
        with pytest.raises(ConflictError):
            await warehouse.delete_transfer(transfer_id)
        assert session.get(Transfer, transfer_id) is not None
    assert session.scalars(select(PartLot.quantity_remaining)).all() == [5]
