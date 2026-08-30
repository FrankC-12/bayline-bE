import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.parts.models import Part
from app.modules.parts.service import _sync_availability
from app.modules.post_ventas.models import LaborSettings, Tempario
from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus, TransferStatus, UpsellStatus
from app.modules.service_orders.exceptions import (
    BayNotFoundError,
    InvalidStatusTransitionError,
    ServiceOrderNotFoundError,
    TaskNotFoundError,
    TransferNotFoundError,
    UpsellNotFoundError,
)
from app.modules.service_orders.models import (
    Bay,
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    Upsell,
)
from app.modules.service_orders.schemas import (
    BayCreate,
    BayUpdate,
    OrderSummary,
    ServiceOrderCreate,
    ServiceOrderUpdate,
    TaskRead,
    TransferLineRead,
    TransferRead,
    UpsellCreate,
    UpsellRead,
)

ALLOWED_TRANSITIONS: dict[ServiceOrderStatus, set[ServiceOrderStatus]] = {
    ServiceOrderStatus.PENDIENTE: {ServiceOrderStatus.EN_PROGRESO, ServiceOrderStatus.CANCELADO},
    ServiceOrderStatus.EN_PROGRESO: {ServiceOrderStatus.COMPLETADO, ServiceOrderStatus.CANCELADO},
    ServiceOrderStatus.COMPLETADO: {ServiceOrderStatus.ORDEN_CERRADA},
    ServiceOrderStatus.ORDEN_CERRADA: set(),
    ServiceOrderStatus.CANCELADO: set(),
}

ACTIVE_STATUSES = [
    ServiceOrderStatus.PENDIENTE,
    ServiceOrderStatus.EN_PROGRESO,
    ServiceOrderStatus.COMPLETADO,
]
HISTORY_STATUSES = [ServiceOrderStatus.ORDEN_CERRADA, ServiceOrderStatus.CANCELADO]


class ServiceOrderService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_orders(
        self,
        filial_id: uuid.UUID,
        statuses: list[ServiceOrderStatus] | None = None,
        scheduled_date: date | None = None,
    ) -> list[ServiceOrder]:
        query = select(ServiceOrder).where(ServiceOrder.filial_id == filial_id)
        if statuses:
            query = query.where(ServiceOrder.status.in_(statuses))
        if scheduled_date is not None:
            query = query.where(func.date(ServiceOrder.scheduled_at) == scheduled_date)
        query = query.order_by(ServiceOrder.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_order(self, order_id: uuid.UUID) -> ServiceOrder:
        order = await self.db.get(ServiceOrder, order_id)
        if order is None:
            raise ServiceOrderNotFoundError(str(order_id))
        return order

    async def create_order(self, payload: ServiceOrderCreate) -> ServiceOrder:
        next_seq = await self._next_sequence_number(payload.filial_id)
        order = ServiceOrder(
            filial_id=payload.filial_id,
            vehicle_id=payload.vehicle_id,
            order_type=payload.order_type,
            notes=payload.notes,
            scheduled_at=payload.scheduled_at,
            technician_user_id=payload.technician_user_id,
            bay_id=payload.bay_id,
            sequence_number=next_seq,
        )
        self.db.add(order)
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def update_order(self, order_id: uuid.UUID, payload: ServiceOrderUpdate) -> ServiceOrder:
        order = await self.get_order(order_id)

        if payload.status and payload.status != order.status:
            if payload.status not in ALLOWED_TRANSITIONS.get(order.status, set()):
                raise InvalidStatusTransitionError(order.status.value, payload.status.value)
            order.status = payload.status
            if payload.status == ServiceOrderStatus.ORDEN_CERRADA:
                order.closed_at = datetime.now(timezone.utc)
                summary = await self.get_order_summary(order_id)
                order.total_amount = summary.total

                # Deferred import avoids a circular import (administracion also
                # reads from service_orders for Rentabilidad).
                from app.modules.administracion.service import AdministracionService

                admin_service = AdministracionService(self.db)
                await admin_service.record_automatic_income(
                    order.filial_id,
                    f"Facturación de orden de servicio · {order.code}",
                    summary.total,
                    order.code,
                )

        if payload.order_type is not None:
            order.order_type = payload.order_type

        if payload.clear_technician:
            order.technician_user_id = None
        elif payload.technician_user_id is not None:
            order.technician_user_id = payload.technician_user_id

        if payload.clear_advisor:
            order.advisor_user_id = None
        elif payload.advisor_user_id is not None:
            order.advisor_user_id = payload.advisor_user_id

        if payload.clear_bay:
            order.bay_id = None
        elif payload.bay_id is not None:
            await self.get_bay(payload.bay_id)
            order.bay_id = payload.bay_id

        if payload.scheduled_at is not None:
            order.scheduled_at = payload.scheduled_at

        if payload.notes is not None:
            order.notes = payload.notes

        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def delete_order(self, order_id: uuid.UUID) -> None:
        order = await self.get_order(order_id)
        await self.db.delete(order)
        await self.db.commit()

    async def _next_sequence_number(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(ServiceOrder.sequence_number)).where(ServiceOrder.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 2000) + 1

    # Bays

    async def list_bays(self, filial_id: uuid.UUID) -> list[Bay]:
        result = await self.db.execute(
            select(Bay).where(Bay.filial_id == filial_id).order_by(Bay.created_at)
        )
        return list(result.scalars().all())

    async def get_bay(self, bay_id: uuid.UUID) -> Bay:
        bay = await self.db.get(Bay, bay_id)
        if bay is None:
            raise BayNotFoundError(str(bay_id))
        return bay

    async def create_bay(self, filial_id: uuid.UUID, payload: BayCreate) -> Bay:
        bay = Bay(filial_id=filial_id, name=payload.name)
        self.db.add(bay)
        await self.db.commit()
        await self.db.refresh(bay)
        return bay

    async def update_bay(self, bay_id: uuid.UUID, payload: BayUpdate) -> Bay:
        bay = await self.get_bay(bay_id)
        if payload.name is not None:
            bay.name = payload.name
        if payload.is_active is not None:
            bay.is_active = payload.is_active
        await self.db.commit()
        await self.db.refresh(bay)
        return bay

    # Tasks (Tareas a realizar)

    async def list_tasks(self, service_order_id: uuid.UUID) -> list[ServiceOrderTask]:
        result = await self.db.execute(
            select(ServiceOrderTask)
            .where(ServiceOrderTask.service_order_id == service_order_id)
            .order_by(ServiceOrderTask.created_at)
        )
        return list(result.scalars().all())

    async def add_task(self, service_order_id: uuid.UUID, tempario_id: uuid.UUID) -> ServiceOrderTask:
        tempario_result = await self.db.execute(
            select(Tempario).options(selectinload(Tempario.parts)).where(Tempario.id == tempario_id)
        )
        tempario = tempario_result.scalar_one_or_none()
        if tempario is None:
            raise TaskNotFoundError(str(tempario_id))

        task = ServiceOrderTask(
            service_order_id=service_order_id,
            tempario_id=tempario.id,
            code_snapshot=tempario.code,
            name_snapshot=tempario.name,
            hours_snapshot=tempario.estimated_hours,
        )
        self.db.add(task)

        # Auto-add this tempario's catalog-linked parts to the pending ODT.
        linked_parts = [p for p in tempario.parts if p.part_id is not None]
        if linked_parts:
            transfer = await self._get_or_create_pending_transfer(service_order_id)
            for tp in linked_parts:
                part = await self.db.get(Part, tp.part_id)
                if part is not None:
                    await self._add_line_to_transfer(transfer, part.id, tp.quantity, float(part.price))

        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def get_task_filial(self, task_id: uuid.UUID) -> uuid.UUID:
        task = await self.db.get(ServiceOrderTask, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        order = await self.get_order(task.service_order_id)
        return order.filial_id

    async def update_task_status(self, task_id: uuid.UUID, status: TaskStatus) -> ServiceOrderTask:
        task = await self.db.get(ServiceOrderTask, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        task.status = status
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def delete_task(self, task_id: uuid.UUID) -> None:
        task = await self.db.get(ServiceOrderTask, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        await self.db.delete(task)
        await self.db.commit()

    # Transfers (Órdenes de Transferencia / ODT)

    async def list_transfers(self, service_order_id: uuid.UUID) -> list[ServiceOrderTransfer]:
        result = await self.db.execute(
            select(ServiceOrderTransfer)
            .options(selectinload(ServiceOrderTransfer.lines))
            .where(ServiceOrderTransfer.service_order_id == service_order_id)
            .order_by(ServiceOrderTransfer.sequence_number)
        )
        return list(result.scalars().all())

    async def add_transfer_line(
        self, service_order_id: uuid.UUID, part_id: uuid.UUID, quantity: int
    ) -> ServiceOrderTransfer:
        part = await self.db.get(Part, part_id)
        if part is None:
            raise TransferNotFoundError(str(part_id))
        transfer = await self._get_or_create_pending_transfer(service_order_id)
        await self._add_line_to_transfer(transfer, part.id, quantity, float(part.price))
        await self.db.commit()
        await self.db.refresh(transfer)
        return transfer

    async def get_transfer_filial(self, transfer_id: uuid.UUID) -> uuid.UUID:
        transfer = await self.db.get(ServiceOrderTransfer, transfer_id)
        if transfer is None:
            raise TransferNotFoundError(str(transfer_id))
        order = await self.get_order(transfer.service_order_id)
        return order.filial_id

    async def mark_transfer_ordered(
        self, transfer_id: uuid.UUID, fulfilled_by_user_id: uuid.UUID | None = None
    ) -> ServiceOrderTransfer:
        result = await self.db.execute(
            select(ServiceOrderTransfer)
            .options(selectinload(ServiceOrderTransfer.lines))
            .where(ServiceOrderTransfer.id == transfer_id)
        )
        transfer = result.scalar_one_or_none()
        if transfer is None:
            raise TransferNotFoundError(str(transfer_id))

        if transfer.status == TransferStatus.PENDIENTE:
            for line in transfer.lines:
                part = await self.db.get(Part, line.part_id)
                if part is not None:
                    part.stock_quantity = max(0, part.stock_quantity - line.quantity)
                    _sync_availability(part)
            transfer.status = TransferStatus.PEDIDO
            transfer.fulfilled_by_user_id = fulfilled_by_user_id
            transfer.fulfilled_at = datetime.now(timezone.utc)

        await self.db.commit()
        await self.db.refresh(transfer)
        return transfer

    async def _get_or_create_pending_transfer(
        self, service_order_id: uuid.UUID
    ) -> ServiceOrderTransfer:
        result = await self.db.execute(
            select(ServiceOrderTransfer)
            .where(
                ServiceOrderTransfer.service_order_id == service_order_id,
                ServiceOrderTransfer.status == TransferStatus.PENDIENTE,
            )
            .order_by(ServiceOrderTransfer.sequence_number.desc())
        )
        transfer = result.scalars().first()
        if transfer is not None:
            return transfer

        seq_result = await self.db.execute(
            select(func.max(ServiceOrderTransfer.sequence_number)).where(
                ServiceOrderTransfer.service_order_id == service_order_id
            )
        )
        next_seq = (seq_result.scalar() or 0) + 1
        transfer = ServiceOrderTransfer(service_order_id=service_order_id, sequence_number=next_seq)
        self.db.add(transfer)
        await self.db.flush()
        return transfer

    async def _add_line_to_transfer(
        self, transfer: ServiceOrderTransfer, part_id: uuid.UUID, quantity: int, unit_price: float
    ) -> None:
        # Query directly instead of touching transfer.lines — for a transfer that
        # was just created in this same call, that relationship isn't loaded yet
        # and accessing it triggers a lazy-load SQLAlchemy's async session can't
        # run implicitly (MissingGreenlet).
        result = await self.db.execute(
            select(ServiceOrderTransferLine).where(
                ServiceOrderTransferLine.transfer_id == transfer.id,
                ServiceOrderTransferLine.part_id == part_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.quantity += quantity
        else:
            self.db.add(
                ServiceOrderTransferLine(
                    transfer_id=transfer.id, part_id=part_id, quantity=quantity, unit_price=unit_price
                )
            )

    # Pricing summary

    async def get_order_summary(self, service_order_id: uuid.UUID) -> OrderSummary:
        order = await self.get_order(service_order_id)

        tasks = await self.list_tasks(service_order_id)
        transfers = await self.list_transfers(service_order_id)

        settings_result = await self.db.execute(
            select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
        )
        settings = settings_result.scalar_one_or_none()
        hourly_rate = float(settings.hourly_rate) if settings else 25.0
        iva_percentage = float(settings.iva_percentage) if settings else 16.0

        labor_subtotal = sum(float(t.hours_snapshot) for t in tasks) * hourly_rate
        parts_subtotal = sum(
            sum(line.quantity * float(line.unit_price) for line in tr.lines) for tr in transfers
        )
        iva_amount = (labor_subtotal + parts_subtotal) * iva_percentage / 100
        total = labor_subtotal + parts_subtotal + iva_amount

        return OrderSummary(
            tasks=[
                TaskRead(
                    id=t.id,
                    tempario_id=t.tempario_id,
                    code_snapshot=t.code_snapshot,
                    name_snapshot=t.name_snapshot,
                    hours_snapshot=float(t.hours_snapshot),
                    status=t.status,
                    created_at=t.created_at,
                )
                for t in tasks
            ],
            transfers=[
                TransferRead(
                    id=tr.id,
                    code=f"{order.code}-{tr.code}",
                    status=tr.status,
                    lines=[
                        TransferLineRead(
                            id=line.id,
                            part_id=line.part_id,
                            quantity=line.quantity,
                            unit_price=float(line.unit_price),
                            subtotal=line.quantity * float(line.unit_price),
                        )
                        for line in tr.lines
                    ],
                    subtotal=sum(line.quantity * float(line.unit_price) for line in tr.lines),
                    created_at=tr.created_at,
                )
                for tr in transfers
            ],
            parts_subtotal=parts_subtotal,
            labor_subtotal=labor_subtotal,
            iva_percentage=iva_percentage,
            iva_amount=iva_amount,
            total=total,
        )

    # Upsells

    async def list_upsells(self, filial_id: uuid.UUID) -> list[Upsell]:
        """All upsells across every order in the filial — this is a filial-wide
        list, not scoped to a single ODS."""
        result = await self.db.execute(
            select(Upsell)
            .join(ServiceOrder, ServiceOrder.id == Upsell.service_order_id)
            .where(ServiceOrder.filial_id == filial_id)
            .order_by(Upsell.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_upsell(self, upsell_id: uuid.UUID) -> Upsell:
        upsell = await self.db.get(Upsell, upsell_id)
        if upsell is None:
            raise UpsellNotFoundError(str(upsell_id))
        return upsell

    async def create_upsell(self, service_order_id: uuid.UUID, payload: UpsellCreate) -> Upsell:
        upsell = Upsell(
            service_order_id=service_order_id,
            title=payload.title,
            description=payload.description,
            evidence_count=payload.evidence_count,
            detected_by_user_id=payload.detected_by_user_id,
        )
        self.db.add(upsell)
        await self.db.commit()
        await self.db.refresh(upsell)
        return upsell

    async def update_upsell_status(self, upsell_id: uuid.UUID, status: UpsellStatus) -> Upsell:
        upsell = await self.get_upsell(upsell_id)
        upsell.status = status
        if status != UpsellStatus.PENDIENTE:
            upsell.resolved_at = datetime.now(timezone.utc)
        else:
            upsell.resolved_at = None
        await self.db.commit()
        await self.db.refresh(upsell)
        return upsell