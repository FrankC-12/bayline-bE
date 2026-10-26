"""One chronology for odometer validation, including audited corrections."""

from sqlalchemy import select

from app.core.exceptions import ConflictError
from app.modules.clients.models import Vehicle


async def validate_mileage(
    db, vehicle_id, mileage, correction_reason=None, exclude_inspection_id=None
):
    if mileage is None:
        return
    if mileage > 1_500_000 and not (correction_reason and correction_reason.strip()):
        raise ConflictError(
            "El kilometraje supera el rango habitual. Requiere una corrección justificada por Administración.",
            error_code="mileage_out_of_range",
        )
    from datetime import UTC

    from app.modules.inspections.models import PreliminaryInspection
    from app.modules.service_orders.enums import ServiceOrderStatus
    from app.modules.service_orders.models import ServiceOrder

    vehicle = (
        await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update())
    ).scalar_one_or_none()
    if vehicle is None:
        return  # The caller reports the missing vehicle.
    inspections = select(PreliminaryInspection).where(
        PreliminaryInspection.vehicle_id == vehicle_id, PreliminaryInspection.mileage.is_not(None)
    )
    if exclude_inspection_id:
        inspections = inspections.where(PreliminaryInspection.id != exclude_inspection_id)
    inspection = (
        (await db.execute(inspections.order_by(PreliminaryInspection.created_at.desc()).limit(1)))
        .scalars()
        .first()
    )
    departure = (
        (
            await db.execute(
                select(ServiceOrder)
                .where(
                    ServiceOrder.vehicle_id == vehicle_id,
                    ServiceOrder.status == ServiceOrderStatus.ORDEN_CERRADA,
                    ServiceOrder.exit_mileage.is_not(None),
                )
                .order_by(ServiceOrder.closed_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    utc = lambda t: t if t.tzinfo else t.replace(tzinfo=UTC)
    previous = inspection.mileage if inspection else vehicle.mileage
    if (
        departure
        and departure.closed_at
        and (not inspection or utc(departure.closed_at) >= utc(inspection.created_at))
    ):
        previous = departure.exit_mileage
    if (
        previous is not None
        and mileage < previous
        and not (correction_reason and correction_reason.strip())
    ):
        raise ConflictError(
            f"El kilometraje ({mileage}) es menor al último registrado ({previous}). Indica mileage_correction_reason para justificar la corrección.",
            error_code="mileage_inconsistent",
            details=[{"field": "mileage", "message": f"Último registro: {previous} km"}],
        )
