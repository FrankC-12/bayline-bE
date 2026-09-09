"""Serialize order mutations with invoicing and reject terminal orders."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.exceptions import (
    ServiceOrderNotFoundError,
    ServiceOrderReadOnlyError,
)
from app.modules.service_orders.models import ServiceOrder


async def require_editable_order(
    db: AsyncSession, order_id: uuid.UUID, *, allow_invoiced: bool = False
) -> ServiceOrder:
    result = await db.execute(
        select(ServiceOrder)
        .where(ServiceOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    order = result.scalar_one_or_none()
    if order is None:
        raise ServiceOrderNotFoundError(str(order_id))
    if order.status in {ServiceOrderStatus.ORDEN_CERRADA, ServiceOrderStatus.CANCELADO}:
        raise ServiceOrderReadOnlyError()
    if order.invoiced_at is not None and not allow_invoiced:
        raise ServiceOrderReadOnlyError()
    return order
