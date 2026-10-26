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
        .with_for_update(of=ServiceOrder)
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


async def require_no_active_visit(db, vehicle_id, filial_id, exclude_id=None):
    """Serialize reception for a vehicle; drafts do not represent a workshop visit."""
    from app.core.exceptions import ConflictError
    from app.modules.clients.models import Vehicle
    from app.modules.filiales.models import Filial

    await db.execute(select(Filial.id).where(Filial.id == filial_id).with_for_update())
    vehicle = (
        await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update())
    ).scalar_one_or_none()
    if vehicle is not None and not vehicle.vin:
        raise ConflictError(
            "Completa el VIN del vehículo antes de enviar la visita al taller.",
            error_code="vehicle_vin_required",
        )
    query = select(ServiceOrder).where(
        (
            ServiceOrder.vehicle_id.in_(select(Vehicle.id).where(Vehicle.vin == vehicle.vin))
            if vehicle is not None and vehicle.vin
            else ServiceOrder.vehicle_id == vehicle_id
        ),
        ServiceOrder.filial_id == filial_id,
        ServiceOrder.is_draft.is_(False),
        ServiceOrder.status.in_(
            [
                ServiceOrderStatus.PENDIENTE,
                ServiceOrderStatus.EN_PROGRESO,
                ServiceOrderStatus.COMPLETADO,
            ]
        ),
    )
    if exclude_id:
        query = query.where(ServiceOrder.id != exclude_id)
    existing = (
        await db.execute(query.order_by(ServiceOrder.created_at).limit(1))
    ).scalar_one_or_none()
    if existing:
        raise ConflictError(
            f"El vehículo ya tiene una visita activa: {existing.code}. Continúa esa orden antes de abrir otra.",
            error_code="vehicle_has_active_order",
        )
