"""Pending-parts checks stay bounded and see uncommitted workflow edits."""

import uuid
from datetime import UTC, datetime

import pytest
from test_part_sales_fifo import inventory as inventory_fixture
from test_parts_discounts import order_inventory as order_inventory_fixture

from app.modules.service_orders.enums import TransferStatus
from app.modules.service_orders.models import ServiceOrderTransferLine

inventory = inventory_fixture
order_inventory = order_inventory_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("line_count", [0, 1, 600])
async def test_pending_lookup_uses_one_select_regardless_of_line_count(
    order_inventory, monkeypatch, line_count
):
    service, session, order, part_id, _ = order_inventory
    request = await service._get_or_create_pending_transfer(order.id)
    session.add_all(
        ServiceOrderTransferLine(transfer_id=request.id, part_id=part_id, quantity=1)
        for _ in range(line_count)
    )
    session.commit()
    execute = service.db.execute
    selects = []

    async def track(statement):
        if statement.is_select:
            selects.append(statement)
        return await execute(statement)

    monkeypatch.setattr(service.db, "execute", track)

    assert await service._has_pending_transfer_pickup(order.id) is bool(line_count)
    assert len(selects) == 1
    assert await service._has_pending_transfer_pickup(uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_pending_lookup_sees_uncommitted_pickup_and_line_removal(order_inventory):
    service, session, order, part_id, _ = order_inventory
    request = await service.add_transfer_line(order.id, part_id, 1)
    assert await service._has_pending_transfer_pickup(order.id) is True

    # Warehouse completion alone is not a technician pickup.
    request.status = TransferStatus.COMPLETADO
    assert await service._has_pending_transfer_pickup(order.id) is True
    request.picked_up_at = datetime.now(UTC)
    assert await service._has_pending_transfer_pickup(order.id) is False

    request.picked_up_at = None
    assert await service._has_pending_transfer_pickup(order.id) is True
    for line in list(request.lines):
        session.delete(line)
    assert await service._has_pending_transfer_pickup(order.id) is False
