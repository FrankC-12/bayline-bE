"""Explicit completed inspection records for tests that start execution."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from app.modules.inspections.models import PreliminaryInspection
from app.modules.inspections.enums import InspectionStatus
from app.modules.detailed_inspections.models import DetailedInspection


async def inspections_ready(service, order):
    db = service.db
    existing = (
        await db.execute(
            select(PreliminaryInspection).where(PreliminaryInspection.service_order_id == order.id)
        )
    ).scalar_one_or_none()
    if existing:
        existing.status = InspectionStatus.COMPLETADA
    else:
        db.add(
            PreliminaryInspection(
                filial_id=order.filial_id,
                vehicle_id=order.vehicle_id,
                service_order_id=order.id,
                inspector_user_id=uuid.uuid4(),
                status=InspectionStatus.COMPLETADA,
                notes="Recepción verificada",
                mileage=10000,
            )
        )
    detailed = (
        await db.execute(
            select(DetailedInspection).where(DetailedInspection.service_order_id == order.id)
        )
    ).scalar_one_or_none()
    if detailed:
        detailed.status = "completada"
    else:
        db.add(
            DetailedInspection(
                service_order_id=order.id,
                status="completada",
                data={},
                annexes=[],
                created_by_user_id=uuid.uuid4(),
                updated_by_user_id=uuid.uuid4(),
                completed_at=datetime.now(UTC),
            )
        )
    await db.commit()


async def task_ready(service, order, task):
    from app.modules.service_orders.models import ServiceOrderTransfer

    await inspections_ready(service, order)
    transfers = (
        (
            await service.db.execute(
                select(ServiceOrderTransfer).where(
                    ServiceOrderTransfer.service_order_id == order.id
                )
            )
        )
        .scalars()
        .all()
    )
    for transfer in transfers:
        transfer.picked_up_at = datetime.now(UTC)
    task.timer_accumulated_seconds = 60
    await service.db.commit()
