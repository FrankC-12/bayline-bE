"""Administrative ODT closure; it never performs a dispatch or pickup."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.service_orders.models import ServiceOrder, ServiceOrderTransfer
from app.modules.warehouse.enums import TransferStatus
from app.modules.warehouse.models import Transfer


async def close_order_transfers(
    db: AsyncSession, order: ServiceOrder, actor_id: uuid.UUID | None
) -> None:
    """Caller holds the filial and ODS locks and owns the transaction.

    Keep warehouse fulfillment evidence intact: closure does not imply that
    stock was consumed, or that a technician supplied a pickup photograph.
    """
    requests = list(
        (
            await db.execute(
                select(ServiceOrderTransfer)
                .where(ServiceOrderTransfer.service_order_id == order.id)
                .order_by(ServiceOrderTransfer.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    if not requests:
        return
    for request in requests:
        if request.closed_at is None:
            request.closed_at = order.closed_at
            request.closed_by_user_id = actor_id
    # Unreceived warehouse relocations only express intent. Cancel them so
    # warehouse staff cannot move stock for work that has already been closed.
    pending = (
        await db.execute(
            select(Transfer)
            .where(
                Transfer.workshop_request_id.in_([r.id for r in requests]),
                Transfer.status.in_([TransferStatus.PEDIDO, TransferStatus.EN_PROCESO]),
            )
            .order_by(Transfer.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalars()
    for transfer in pending:
        transfer.status = TransferStatus.CANCELADA
        note = f"Cierre automático de {order.code}; solicitud de repuestos cerrada."
        transfer.note = f"{transfer.note}\n{note}" if transfer.note else note
