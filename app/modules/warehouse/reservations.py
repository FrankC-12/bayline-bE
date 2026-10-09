"""Stock pledged to workshop requests is unavailable to other consumers."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select


async def reserved_by_lot(db, part_id, *, exclude_transfer_id=None, exclude_line_id=None):
    from app.modules.service_orders.enums import ServiceOrderStatus, TransferStatus
    from app.modules.service_orders.models import (
        ServiceOrder,
    )
    from app.modules.service_orders.models import (
        ServiceOrderTransfer as Request,
    )
    from app.modules.service_orders.models import (
        ServiceOrderTransferLine as Line,
    )
    from app.modules.service_orders.models import (
        ServiceOrderTransferLotAllocation as Allocation,
    )
    from app.modules.warehouse.models import PartLot

    query = (
        select(Allocation.lot_id, func.sum(Allocation.quantity))
        .join(PartLot, PartLot.id == Allocation.lot_id)
        .join(Line, Line.id == Allocation.transfer_line_id)
        .join(Request, Request.id == Line.transfer_id)
        .join(ServiceOrder, ServiceOrder.id == Request.service_order_id)
        .where(
            PartLot.part_id == part_id,
            Request.stock_deducted.is_(False),
            ServiceOrder.status != ServiceOrderStatus.CANCELADO,
            or_(
                Request.status == TransferStatus.PEDIDO,
                and_(
                    Request.status == TransferStatus.PENDIENTE,
                    Allocation.created_at >= datetime.now(UTC) - timedelta(minutes=5),
                ),
            ),
        )
        .group_by(Allocation.lot_id)
    )
    if exclude_transfer_id is not None:
        query = query.where(Request.id != exclude_transfer_id)
    if exclude_line_id is not None:
        query = query.where(Line.id != exclude_line_id)
    return dict((await db.execute(query)).all())


@dataclass
class AvailableLot:
    id: object
    warehouse_id: object
    unit_cost: object
    quantity_remaining: int


def available_lots(lots, reservations):
    return [
        AvailableLot(
            lot.id,
            lot.warehouse_id,
            lot.unit_cost,
            max(0, lot.quantity_remaining - reservations.get(lot.id, 0)),
        )
        for lot in lots
    ]
