"""Web draft lifecycle. Drafts never dispatch stock or appear in mobile work lists."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.roles.enums import AccessLevel
from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus, UpsellStatus
from app.modules.service_orders.guards import require_editable_order
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTransfer,
    Upsell,
)


class DraftService:
    def __init__(self, db):
        self.db = db

    async def _vehicle(self, vehicle_id, filial_id):
        if vehicle_id is None:
            return
        found = (
            await self.db.execute(
                select(Vehicle.id)
                .join(Client, Client.id == Vehicle.client_id)
                .where(Vehicle.id == vehicle_id, Client.filial_id == filial_id)
            )
        ).scalar_one_or_none()
        if found is None:
            raise BadRequestError("El vehículo no pertenece a esta filial.")

    async def _bay(self, bay_id, filial_id):
        from app.modules.service_orders.models import Bay

        if bay_id:
            bay = await self.db.get(Bay, bay_id)
            if bay is None or bay.filial_id != filial_id or not bay.is_active:
                raise BadRequestError("Selecciona una bahía activa de esta filial.")

    async def _schedule(self, filial_id, bay_id, scheduled_at, exclude_id=None):
        if not bay_id or not scheduled_at:
            return
        # Serialize free-slot selection across web sessions. Existing overlapping
        # visits remain readable in the calendar, but cannot be newly double-booked.
        await self.db.execute(select(Filial.id).where(Filial.id == filial_id).with_for_update())
        duration = func.sum(ServiceOrderTask.hours_snapshot)
        grouped = (
            select(ServiceOrderTask.service_order_id, duration.label("hours"))
            .where(ServiceOrderTask.status != TaskStatus.CANCELADA)
            .group_by(ServiceOrderTask.service_order_id)
            .subquery()
        )
        rows = (
            await self.db.execute(
                select(ServiceOrder.id, ServiceOrder.scheduled_at, grouped.c.hours)
                .outerjoin(grouped, grouped.c.service_order_id == ServiceOrder.id)
                .where(
                    ServiceOrder.filial_id == filial_id,
                    ServiceOrder.bay_id == bay_id,
                    ServiceOrder.scheduled_at.is_not(None),
                    ServiceOrder.status.notin_(
                        [ServiceOrderStatus.CANCELADO, ServiceOrderStatus.ORDEN_CERRADA]
                    ),
                )
            )
        ).all()
        own_hours = next((float(hours or 0) for key, _, hours in rows if key == exclude_id), 0)
        if exclude_id and not own_hours:
            own_hours = float(
                (
                    await self.db.execute(
                        select(func.sum(ServiceOrderTask.hours_snapshot)).where(
                            ServiceOrderTask.service_order_id == exclude_id,
                            ServiceOrderTask.status != TaskStatus.CANCELADA,
                        )
                    )
                ).scalar()
                or 0
            )
        start = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=UTC)
        end = start + timedelta(hours=max(0.5, own_hours))
        for key, other_start, hours in rows:
            if key == exclude_id:
                continue
            other_start = other_start if other_start.tzinfo else other_start.replace(tzinfo=UTC)
            if (
                start < other_start + timedelta(hours=max(0.5, float(hours or 0)))
                and end > other_start
            ):
                raise ConflictError(
                    "La bahía ya tiene una visita en ese horario.",
                    error_code="bay_schedule_conflict",
                )

    async def create(self, payload, actor):
        from app.modules.ajustes.snapshots import settings_snapshot
        from app.modules.post_ventas.service import PostVentasService
        from app.modules.service_orders.service import ServiceOrderService

        service = ServiceOrderService(self.db)
        await self._vehicle(payload.vehicle_id, payload.filial_id)
        await self._bay(payload.bay_id, payload.filial_id)
        await self.db.execute(
            select(Filial.id).where(Filial.id == payload.filial_id).with_for_update()
        )
        await self._schedule(payload.filial_id, payload.bay_id, payload.scheduled_at)
        kind = await service._resolve_order_type(payload.filial_id, None)
        order = ServiceOrder(
            filial_id=payload.filial_id,
            vehicle_id=payload.vehicle_id,
            order_type_id=kind.id,
            advisor_user_id=actor.user_id,
            is_draft=True,
            creation_source="web",
            scheduled_at=payload.scheduled_at,
            bay_id=payload.bay_id,
            sequence_number=await service._next_sequence_number(payload.filial_id),
            settings_snapshot=settings_snapshot(
                await PostVentasService(self.db).get_labor_settings(payload.filial_id, commit=False)
            ),
        )
        self.db.add(order)
        await self.db.flush()
        if payload.upsell_id:
            finding = (
                await self.db.execute(
                    select(Upsell).where(Upsell.id == payload.upsell_id).with_for_update()
                )
            ).scalar_one_or_none()
            if finding is None:
                raise NotFoundError("Recomendación no encontrada.")
            origin = await service.get_order(finding.service_order_id)
            if (
                origin.vehicle_id != order.vehicle_id
                or origin.filial_id != order.filial_id
                or finding.status not in (UpsellStatus.PENDIENTE, UpsellStatus.POSPUESTO)
            ):
                raise ConflictError("Esta recomendación no se puede aplicar al borrador.")
            transfer = await service._get_or_create_pending_transfer(order.id, force_new=True)
            task_ids = []
            for task in finding.tasks:
                added = await service.add_task(
                    order.id, task.tempario_id, transfer=transfer, commit=False
                )
                task_ids.append(str(added.id))
            for part in finding.parts:
                await service.add_transfer_line(
                    order.id, part.part_id, part.quantity, transfer=transfer, commit=False
                )
            await self.db.flush()
            from app.modules.service_orders.models import ServiceOrderTransferLine

            line_ids = list(
                (
                    await self.db.execute(
                        select(ServiceOrderTransferLine.id).where(
                            ServiceOrderTransferLine.transfer_id == transfer.id
                        )
                    )
                ).scalars()
            )
            if not line_ids:
                await self.db.delete(transfer)
            order.draft_upsells = [
                {
                    "upsell_id": str(finding.id),
                    "task_ids": task_ids,
                    "line_ids": [str(i) for i in line_ids],
                }
            ]
        await self.db.commit()
        return await service.get_order(order.id)

    async def editable(self, order_id):
        order = await require_editable_order(self.db, order_id)
        if not order.is_draft:
            raise ConflictError("La orden ya fue enviada al taller.", error_code="order_not_draft")
        return order

    async def update(self, order_id, payload):
        order = await self.editable(order_id)
        if (
            payload.expected_revision is not None
            and payload.expected_revision != order.draft_revision
        ):
            raise ConflictError(
                "El borrador cambió en otra ventana. Recarga antes de guardar.",
                error_code="draft_revision_conflict",
            )
        fields = payload.model_fields_set - {"expected_revision"}
        if "vehicle_id" in fields and payload.vehicle_id != order.vehicle_id:
            has_tasks = (
                await self.db.execute(
                    select(ServiceOrderTask.id)
                    .where(ServiceOrderTask.service_order_id == order.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if has_tasks:
                raise ConflictError("Quita las tareas antes de cambiar el vehículo.")
            await self._vehicle(payload.vehicle_id, order.filial_id)
        if "bay_id" in fields:
            await self._bay(payload.bay_id, order.filial_id)
        if payload.technician_user_id:
            await self._technician(payload.technician_user_id, order.filial_id)
        new_bay = payload.bay_id if "bay_id" in fields else order.bay_id
        new_date = payload.scheduled_at if "scheduled_at" in fields else order.scheduled_at
        if new_bay != order.bay_id or new_date != order.scheduled_at:
            await self._schedule(order.filial_id, new_bay, new_date, order.id)
        for key in fields:
            setattr(order, key, getattr(payload, key))
        order.draft_revision += 1
        await self.db.commit()
        from app.modules.service_orders.service import ServiceOrderService

        return await ServiceOrderService(self.db).get_order(order.id)

    async def _technician(self, user_id, filial_id):
        from app.modules.auth.schemas import CurrentUser
        from app.modules.roles.models import Role
        from app.modules.roles.permissions import ensure_module_access
        from app.modules.users.enums import UserStatus
        from app.modules.users.models import User

        user = await self.db.get(User, user_id)
        if user is None or user.filial_id != filial_id or user.status != UserStatus.ACTIVO:
            raise BadRequestError("Selecciona un técnico activo de esta filial.")
        role = await self.db.get(Role, user.role_id)
        actor = CurrentUser(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role_id=role.id,
            role_slug=role.slug,
            scope=role.scope,
            holding_id=user.holding_id,
            filial_id=user.filial_id,
        )
        await ensure_module_access(
            self.db, actor, filial_id, "tecnico-servicio", AccessLevel.EDITAR
        )

    async def submit(self, order_id, actor):
        from app.modules.notifications.service import queue_order_event
        from app.modules.service_orders.service import ServiceOrderService

        order = await self.editable(order_id)
        service = ServiceOrderService(self.db)
        tasks = await service.list_tasks(order.id)
        if (
            not order.vehicle_id
            or not order.technician_user_id
            or not any(t.status != TaskStatus.CANCELADA for t in tasks)
        ):
            raise ConflictError(
                "Selecciona vehículo, técnico y al menos una tarea para enviar al taller.",
                error_code="draft_incomplete",
            )
        await self._vehicle(order.vehicle_id, order.filial_id)
        await self._technician(order.technician_user_id, order.filial_id)
        for reference in order.draft_upsells or []:
            finding = (
                await self.db.execute(
                    select(Upsell)
                    .where(Upsell.id == uuid.UUID(reference["upsell_id"]))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if finding is None or finding.status not in (
                UpsellStatus.PENDIENTE,
                UpsellStatus.POSPUESTO,
            ):
                raise ConflictError("Una recomendación ya fue resuelta desde otra orden.")
            if reference["task_ids"] and not set(reference["task_ids"]).issubset(
                {str(t.id) for t in tasks if t.status != TaskStatus.CANCELADA}
            ):
                continue  # The advisor removed the proposed work; keep the recommendation pending.
            from app.modules.service_orders.models import ServiceOrderTransferLine

            current_lines = set(
                str(i)
                for i in (
                    await self.db.execute(
                        select(ServiceOrderTransferLine.id)
                        .join(ServiceOrderTransfer)
                        .where(ServiceOrderTransfer.service_order_id == order.id)
                    )
                ).scalars()
            )
            if not set(reference["line_ids"]).issubset(current_lines):
                continue
            if not reference["task_ids"] and not reference["line_ids"]:
                continue  # Legacy free-text recommendation has no work to approve.
            finding.status = UpsellStatus.APROBADO
            finding.applied_to_service_order_id = order.id
            finding.approved_by_user_id = actor.user_id
            from app.modules.service_orders.enums import UpsellApprovalChannel

            finding.approval_channel = UpsellApprovalChannel.PRESENCIAL
            finding.resolved_by_user_id = actor.user_id
            finding.resolved_at = datetime.now(UTC)
        order.is_draft = False
        order.submitted_at = datetime.now(UTC)
        await queue_order_event(
            self.db,
            order,
            "tecnico_asignado",
            f"{order.id}:{order.technician_user_id}",
            [order.technician_user_id],
        )
        await self.db.commit()
        return await service.get_order(order.id)

    async def discard(self, order_id):
        order = await self.editable(order_id)
        from app.modules.service_orders.models import ServiceOrderTransferLine

        dispatched = (
            await self.db.execute(
                select(ServiceOrderTransfer.id)
                .where(
                    ServiceOrderTransfer.service_order_id == order.id,
                    ServiceOrderTransfer.stock_deducted.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if dispatched:
            raise ConflictError("El borrador tiene movimientos de almacén y no puede eliminarse.")
        # There is no physical inventory movement in a draft. Release its children.
        from sqlalchemy import delete

        transfer_ids = select(ServiceOrderTransfer.id).where(
            ServiceOrderTransfer.service_order_id == order.id
        )
        from app.modules.service_orders.models import ServiceOrderTransferLotAllocation

        line_ids = select(ServiceOrderTransferLine.id).where(
            ServiceOrderTransferLine.transfer_id.in_(transfer_ids)
        )
        await self.db.execute(
            delete(ServiceOrderTransferLotAllocation).where(
                ServiceOrderTransferLotAllocation.transfer_line_id.in_(line_ids)
            )
        )
        await self.db.execute(
            delete(ServiceOrderTransferLine).where(
                ServiceOrderTransferLine.transfer_id.in_(transfer_ids)
            )
        )
        await self.db.execute(
            delete(ServiceOrderTransfer).where(ServiceOrderTransfer.service_order_id == order.id)
        )
        await self.db.execute(
            delete(ServiceOrderTask).where(ServiceOrderTask.service_order_id == order.id)
        )
        await self.db.delete(order)
        await self.db.commit()
