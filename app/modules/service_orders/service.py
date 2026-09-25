import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.models import Vehicle
from app.modules.parts.models import Part
from app.modules.parts.pricing import DEFAULT_DISCOUNT, PARTS_MULTIPLIERS, price_parts_cost
from app.modules.parts.service import _sync_availability
from app.modules.post_ventas.enums import WarrantyPolicyAppliesTo, WarrantyPolicyStatus
from app.modules.post_ventas.models import LaborSettings, Tempario, WarrantyPolicy
from app.modules.service_orders.enums import (
    ReworkFailureCategory,
    ServiceOrderPayer,
    ServiceOrderStatus,
    ServiceOrderType,
    TaskStatus,
    TransferStatus,
    UpsellStatus,
    WarrantyClaimStatus,
    WarrantyClaimType,
)
from app.modules.service_orders.exceptions import (
    BayNotFoundError,
    FailureCategoryRequiredError,
    InvalidStatusTransitionError,
    InvalidTransferStatusTransitionError,
    OrderNotInvoicedError,
    ServiceOrderNotCancelledError,
    ServiceOrderNotFoundError,
    ServiceOrderRequiredForComebackError,
    TaskNotFoundError,
    TransferLineNotEditableError,
    TransferNotFoundError,
    UpsellNotFoundError,
    VehicleWarrantyRequiredError,
    WarrantyClaimAlreadyConvertedError,
    WarrantyClaimAlreadyDecidedError,
    WarrantyClaimNotAuthorizedError,
    WarrantyClaimNotFoundError,
    WarrantyClaimOrderMismatchError,
    WarrantyClaimReferenceMismatchError,
    WarrantyClaimRequiredForOrderTypeError,
    WarrantyOverrideNoteRequiredError,
)
from app.modules.service_orders.guards import require_editable_order
from app.modules.service_orders.models import (
    Bay,
    ServiceOrder,
    ServiceOrderInvoice,
    ServiceOrderTask,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    ServiceOrderTransferLotAllocation,
    Upsell,
    UpsellPart,
    UpsellTask,
    WarrantyClaim,
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
    UpsellDecisionInput,
    UpsellPartRead,
    UpsellRead,
    UpsellTaskRead,
    WarrantyClaimAuthorizationInput,
    WarrantyClaimContext,
    WarrantyClaimConvertInput,
    WarrantyClaimCreate,
    WarrantyClaimRead,
)
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.fifo import allocate_fifo, allocate_fifo_preview
from app.modules.warehouse.models import PartLot, StockMovement
from app.modules.administracion.models import PurchaseRequest, SupplierClaim

# How long an ODT line's FIFO preview also acts as a soft reservation on the
# specific lot units it drew from — long enough to cover the normal
# add-then-dispatch flow, short enough that an abandoned/forgotten ODT
# doesn't lock up inventory indefinitely.
RESERVATION_TTL = timedelta(minutes=5)


@dataclass
class _AvailableLot:
    """A PartLot's id/cost/warehouse with its quantity adjusted for
    reservations held by other pending lines — allocate_fifo/allocate_fifo_preview
    only ever read .quantity_remaining, so this drops in wherever a real
    PartLot would go without risking a stray write to the tracked ORM
    attribute they actually decrement at dispatch."""

    id: uuid.UUID
    unit_cost: float
    warehouse_id: uuid.UUID
    quantity_remaining: int


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

# order_type values that require linking an existing, authorized WarrantyClaim
# of the matching claim_type for the same vehicle — see create_order.
CLAIM_LINKED_ORDER_TYPES: dict[ServiceOrderType, WarrantyClaimType] = {
    ServiceOrderType.GARANTIA_FABRICA: WarrantyClaimType.FABRICA,
    ServiceOrderType.COMEBACK: WarrantyClaimType.COMEBACK,
    ServiceOrderType.CAMPANA: WarrantyClaimType.CAMPANA_RECALL,
}


def _incomplete_completion_message(
    pending_tasks: list["ServiceOrderTask"], pending_transfers: list["ServiceOrderTransfer"]
) -> str:
    parts = []
    if pending_tasks:
        names = ", ".join(t.name_snapshot for t in pending_tasks)
        word = "tarea" if len(pending_tasks) == 1 else "tareas"
        parts.append(f"{len(pending_tasks)} {word} sin terminar ({names})")
    if pending_transfers:
        codes = ", ".join(t.code for t in pending_transfers)
        word = "ODT" if len(pending_transfers) == 1 else "ODTs"
        parts.append(f"{len(pending_transfers)} {word} sin despachar ({codes})")
    return "No se puede completar la orden — pendiente: " + " y ".join(parts) + "."


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

    async def count_transfers_by_status(self, filial_id: uuid.UUID) -> dict[TransferStatus, int]:
        """ServiceOrderTransfer (an "ODT") has no filial_id of its own —
        it's scoped to a filial only through the ServiceOrder it belongs
        to, same join used by billing.py's _warranty_cost."""
        result = await self.db.execute(
            select(ServiceOrderTransfer.status, func.count())
            .select_from(ServiceOrderTransfer)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderTransfer.service_order_id)
            .where(ServiceOrder.filial_id == filial_id)
            .group_by(ServiceOrderTransfer.status)
        )
        return dict(result.all())

    async def get_order(self, order_id: uuid.UUID) -> ServiceOrder:
        order = await self.db.get(ServiceOrder, order_id)
        if order is None:
            raise ServiceOrderNotFoundError(str(order_id))
        return order

    async def create_order(self, payload: ServiceOrderCreate) -> ServiceOrder:
        from app.modules.inspections.models import PreliminaryInspection

        # intake_mileage is always a view inherited from a PreliminaryInspection,
        # never entered by hand. A walk-in ODS (no scheduled_at — the vehicle is
        # physically present) must reference one; an order scheduled ahead of
        # time can't know it yet, so it's linked later from the order detail
        # screen once the vehicle arrives (see InspectionService.update_inspection).
        inspection: PreliminaryInspection | None = None
        if payload.inspection_id is not None:
            inspection = await self.db.get(PreliminaryInspection, payload.inspection_id)
            if inspection is None or inspection.vehicle_id != payload.vehicle_id:
                raise BadRequestError("La inspección preliminar no corresponde a este vehículo.")
            if inspection.service_order_id is not None:
                raise BadRequestError("Esta inspección preliminar ya está vinculada a otra orden.")
        elif payload.scheduled_at is None:
            raise BadRequestError(
                "Se requiere una inspección preliminar para crear la orden.",
                error_code="inspection_required",
            )

        # customer_reason is inherited from the inspection's notes, the same
        # way intake_mileage is — an inspection that has notes always wins
        # over whatever the client sent. The client-sent value is only used
        # as a fallback for a walk-in with no inspection, or an inspection
        # from before notes became required to complete one.
        inherited_reason = inspection.notes.strip() if inspection and inspection.notes else None
        customer_reason = inherited_reason or (
            payload.customer_reason.strip() if payload.customer_reason else None
        )
        if not customer_reason:
            raise BadRequestError(
                "Se requiere el motivo o síntoma reportado por el cliente.",
                error_code="customer_reason_required",
            )

        # garantia_fabrica/comeback/campana must reference an existing,
        # authorized claim for THIS vehicle, of the matching claim_type —
        # otherwise "Reclamo" is meaningless metadata anyone could type.
        required_claim_type = CLAIM_LINKED_ORDER_TYPES.get(payload.order_type)
        if required_claim_type is not None:
            if payload.warranty_claim_id is None:
                raise WarrantyClaimRequiredForOrderTypeError()
            claim = await self.db.get(WarrantyClaim, payload.warranty_claim_id)
            if claim is None:
                raise WarrantyClaimNotFoundError(str(payload.warranty_claim_id))
            if claim.vehicle_id != payload.vehicle_id:
                raise WarrantyClaimOrderMismatchError(
                    "El reclamo seleccionado no corresponde al vehículo de esta orden."
                )
            if claim.claim_type != required_claim_type:
                raise WarrantyClaimOrderMismatchError(
                    "El reclamo seleccionado no corresponde al tipo de orden elegido."
                )
            if claim.status != WarrantyClaimStatus.AUTORIZADO:
                raise WarrantyClaimOrderMismatchError("El reclamo seleccionado todavía no está autorizado.")

        next_seq = await self._next_sequence_number(payload.filial_id)
        order = ServiceOrder(
            filial_id=payload.filial_id,
            vehicle_id=payload.vehicle_id,
            order_type=payload.order_type,
            warranty_claim_id=payload.warranty_claim_id if required_claim_type is not None else None,
            discount_label=payload.discount_label,
            notes=payload.notes,
            scheduled_at=payload.scheduled_at,
            technician_user_id=payload.technician_user_id,
            advisor_user_id=payload.advisor_user_id,
            bay_id=payload.bay_id,
            intake_mileage=inspection.mileage if inspection else None,
            customer_reason=customer_reason,
            promised_at=payload.promised_at,
            sequence_number=next_seq,
        )
        self.db.add(order)
        await self.db.flush()
        if inspection is not None:
            inspection.service_order_id = order.id
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def update_order(
        self,
        order_id: uuid.UUID,
        payload: ServiceOrderUpdate,
        current_user: CurrentUser | None = None,
    ) -> ServiceOrder:
        if payload.status == ServiceOrderStatus.ORDEN_CERRADA:
            if payload.model_fields_set != {"status"}:
                raise BadRequestError("Cerrar la orden es una operación separada de la edición.")
            return await self.close_order(order_id)
        if payload.status == ServiceOrderStatus.CANCELADO:
            raise BadRequestError(
                "Cancelar la orden requiere confirmar un motivo; usa la acción de cancelar."
            )
        order = await require_editable_order(self.db, order_id)

        becoming_completado = (
            payload.status == ServiceOrderStatus.COMPLETADO and order.status != ServiceOrderStatus.COMPLETADO
        )
        if becoming_completado:
            order.completed_at = datetime.now(UTC)
            # A cancelada task was deliberately dropped, not left unfinished —
            # it must not block closing the order any more than a completada one does.
            pending_tasks = [
                t
                for t in await self.list_tasks(order_id)
                if t.status not in (TaskStatus.COMPLETADA, TaskStatus.CANCELADA)
            ]
            # Only PENDIENTE (never dispatched) blocks closing — PEDIDO and
            # COMPLETADO have both already left the shelf, "!= PEDIDO" would
            # wrongly flag an already-Completado ODT as undispatched.
            pending_transfers = [
                t for t in await self.list_transfers(order_id) if t.status == TransferStatus.PENDIENTE
            ]
            if pending_tasks or pending_transfers:
                if not payload.confirm_incomplete_completion:
                    raise BadRequestError(
                        _incomplete_completion_message(pending_tasks, pending_transfers),
                        error_code="order_incomplete",
                    )
                order.completed_with_pending_items = True
                order.completed_override_by_user_id = current_user.user_id if current_user else None
                order.completed_override_at = datetime.now(UTC)

        if payload.discount_label is not None and payload.discount_label != order.discount_label:
            order.discount_label = payload.discount_label
            for transfer in await self.list_transfers(order_id):
                for line in transfer.lines:
                    line.unit_price, line.line_total = price_parts_cost(
                        Decimal(str(line.cost_total)), line.quantity, order.discount_label
                    )

        if payload.status and payload.status != order.status:
            if payload.status not in ALLOWED_TRANSITIONS.get(order.status, set()):
                raise InvalidStatusTransitionError(order.status.value, payload.status.value)
            order.status = payload.status
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

        if payload.clear_labor_warranty_policy:
            order.labor_warranty_policy_id = None
        elif payload.labor_warranty_policy_id is not None:
            await self._validate_warranty_policy(
                payload.labor_warranty_policy_id,
                {WarrantyPolicyAppliesTo.MANO_DE_OBRA, WarrantyPolicyAppliesTo.AMBAS},
            )
            order.labor_warranty_policy_id = payload.labor_warranty_policy_id

        if payload.clear_parts_warranty_policy:
            order.parts_warranty_policy_id = None
        elif payload.parts_warranty_policy_id is not None:
            await self._validate_warranty_policy(
                payload.parts_warranty_policy_id,
                {WarrantyPolicyAppliesTo.REPUESTOS, WarrantyPolicyAppliesTo.AMBAS},
            )
            order.parts_warranty_policy_id = payload.parts_warranty_policy_id

        if payload.scheduled_at is not None:
            order.scheduled_at = payload.scheduled_at

        if payload.notes is not None:
            order.notes = payload.notes

        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def _validate_warranty_policy(
        self, policy_id: uuid.UUID, allowed_applies_to: set[WarrantyPolicyAppliesTo]
    ) -> None:
        policy = (
            await self.db.execute(select(WarrantyPolicy).where(WarrantyPolicy.id == policy_id))
        ).scalar_one_or_none()
        if policy is None:
            raise BadRequestError("La política de garantía seleccionada no existe.", error_code="warranty_policy_not_found")
        if policy.status != WarrantyPolicyStatus.ACTIVA:
            raise BadRequestError(
                "Esa política de garantía está inactiva y no puede seleccionarse.",
                error_code="warranty_policy_inactive",
            )
        if policy.applies_to not in allowed_applies_to:
            raise BadRequestError(
                "Esa política de garantía no aplica a este tipo de cobertura.",
                error_code="warranty_policy_wrong_applies_to",
            )

    async def close_order(
        self,
        order_id: uuid.UUID,
        next_maintenance_due_at: date | None = None,
        next_maintenance_tempario_id: uuid.UUID | None = None,
    ) -> ServiceOrder:
        order = await require_editable_order(self.db, order_id, allow_invoiced=True)
        if order.invoiced_at is None:
            raise BadRequestError(
                "Primero debes facturar y registrar el cobro.", error_code="invoice_required"
            )
        if order.status != ServiceOrderStatus.COMPLETADO:
            raise BadRequestError("Solo se puede cerrar una orden completada y facturada.")
        if next_maintenance_tempario_id is not None:
            tempario = await self.db.get(Tempario, next_maintenance_tempario_id)
            if tempario is None or tempario.filial_id != order.filial_id:
                raise BadRequestError("El tempario del plan no pertenece a esta filial.")
        order.status = ServiceOrderStatus.ORDEN_CERRADA
        order.closed_at = datetime.now(UTC)
        if next_maintenance_due_at is not None or next_maintenance_tempario_id is not None:
            vehicle = await self.db.get(Vehicle, order.vehicle_id)
            if vehicle is not None:
                if next_maintenance_due_at is not None:
                    vehicle.next_maintenance_due_at = next_maintenance_due_at
                if next_maintenance_tempario_id is not None:
                    vehicle.next_maintenance_tempario_id = next_maintenance_tempario_id
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def cancel_order(
        self, order_id: uuid.UUID, reason: str, cancelled_by_user_id: uuid.UUID
    ) -> ServiceOrder:
        order = await require_editable_order(self.db, order_id)
        if ServiceOrderStatus.CANCELADO not in ALLOWED_TRANSITIONS.get(order.status, set()):
            raise InvalidStatusTransitionError(order.status.value, ServiceOrderStatus.CANCELADO.value)
        order.status = ServiceOrderStatus.CANCELADO
        order.cancel_reason = reason
        order.cancelled_by_user_id = cancelled_by_user_id
        order.cancelled_at = datetime.now(UTC)
        # A fresh cancellation starts a new cycle — clear any reopen trail
        # from a previous cycle so it doesn't look stale next to it.
        order.reopened_by_user_id = None
        order.reopened_at = None
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def reopen_order(self, order_id: uuid.UUID, reopened_by_user_id: uuid.UUID) -> ServiceOrder:
        result = await self.db.execute(
            select(ServiceOrder)
            .where(ServiceOrder.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise ServiceOrderNotFoundError(str(order_id))
        if order.status != ServiceOrderStatus.CANCELADO:
            raise ServiceOrderNotCancelledError()
        order.status = ServiceOrderStatus.PENDIENTE
        order.reopened_by_user_id = reopened_by_user_id
        order.reopened_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def delete_order(self, order_id: uuid.UUID) -> None:
        order = await require_editable_order(self.db, order_id)
        await self.db.delete(order)
        await self.db.commit()

    async def _next_sequence_number(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(ServiceOrder.sequence_number)).where(
                ServiceOrder.filial_id == filial_id
            )
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

    async def add_task(
        self,
        service_order_id: uuid.UUID,
        tempario_id: uuid.UUID,
        payer: ServiceOrderPayer = ServiceOrderPayer.CLIENTE,
    ) -> ServiceOrderTask:
        await require_editable_order(self.db, service_order_id)
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
            payer=payer,
        )
        self.db.add(task)
        # Flush so task.id exists before it's used to tag the parts below.
        await self.db.flush()

        # Auto-add this tempario's catalog-linked parts to the pending ODT —
        # they exist only because of this task, so they inherit its payer
        # and are tagged with its id (billing uses this to know the task
        # installed a part, for the separate "repuesto" warranty term).
        # Never blocked by stock — see _add_line_to_transfer.
        warnings: list[str] = []
        linked_parts = [p for p in tempario.parts if p.part_id is not None]
        if linked_parts:
            transfer = await self._get_or_create_pending_transfer(service_order_id)
            for tp in linked_parts:
                part = await self.db.get(Part, tp.part_id)
                if part is not None:
                    warning = await self._add_line_to_transfer(
                        transfer, part.id, tp.quantity, payer=payer, service_order_task_id=task.id
                    )
                    if warning:
                        warnings.append(warning)

        await self.db.commit()
        await self.db.refresh(task)
        # Not a mapped column — a transient hint for the router to surface as
        # a non-blocking warning on this one response, same convention as
        # Vehicle.current_mileage elsewhere in this codebase.
        task.stock_warnings = warnings
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
        await require_editable_order(self.db, task.service_order_id)
        task.status = status
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def update_task_payer(self, task_id: uuid.UUID, payer: ServiceOrderPayer) -> ServiceOrderTask:
        task = await self.db.get(ServiceOrderTask, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        await require_editable_order(self.db, task.service_order_id)
        task.payer = payer
        await self.db.commit()
        await self.db.refresh(task)
        return task

    async def delete_task(self, task_id: uuid.UUID) -> None:
        task = await self.db.get(ServiceOrderTask, task_id)
        if task is None:
            raise TaskNotFoundError(str(task_id))
        await require_editable_order(self.db, task.service_order_id)
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
        self,
        service_order_id: uuid.UUID,
        part_id: uuid.UUID,
        quantity: int,
        payer: ServiceOrderPayer = ServiceOrderPayer.CLIENTE,
    ) -> ServiceOrderTransfer:
        await require_editable_order(self.db, service_order_id)
        part = await self.db.get(Part, part_id)
        if part is None:
            raise TransferNotFoundError(str(part_id))
        transfer = await self._get_or_create_pending_transfer(service_order_id)
        warning = await self._add_line_to_transfer(transfer, part.id, quantity, payer=payer)
        await self.db.commit()
        await self.db.refresh(transfer)
        transfer.stock_warnings = [warning] if warning else []
        return transfer

    async def update_transfer_line_payer(
        self, line_id: uuid.UUID, payer: ServiceOrderPayer
    ) -> ServiceOrderTransferLine:
        line = await self.db.get(ServiceOrderTransferLine, line_id)
        if line is None:
            raise TransferNotFoundError(str(line_id))
        transfer = await self.db.get(ServiceOrderTransfer, line.transfer_id)
        await require_editable_order(self.db, transfer.service_order_id)
        line.payer = payer
        await self.db.commit()
        await self.db.refresh(line)
        return line

    async def set_transfer_line_quantity(self, line_id: uuid.UUID, quantity: int) -> ServiceOrderTransfer:
        """Changes how much of a part is requested — e.g. an oil change line
        for 6 liters drops to 2 once the client says they're bringing 4 of
        their own. Only while the ODT is still Pendiente: once dispatched
        (Pedido), stock has already been decremented against the original
        quantity and the line is frozen (see mark_transfer_ordered)."""
        line = await self.db.get(ServiceOrderTransferLine, line_id)
        if line is None:
            raise TransferNotFoundError(str(line_id))
        transfer = await self.db.get(ServiceOrderTransfer, line.transfer_id)
        await require_editable_order(self.db, transfer.service_order_id)
        if transfer.status != TransferStatus.PENDIENTE:
            raise TransferLineNotEditableError()

        order = await self.get_order(transfer.service_order_id)
        cost, allocations, shortfall = await self._fifo_preview_cost(
            order.filial_id, line.part_id, quantity, exclude_line_id=line.id
        )
        warning: str | None = None
        if shortfall > 0:
            part = await self.db.get(Part, line.part_id)
            available = quantity - shortfall
            warning = (
                f"Stock insuficiente para \"{part.name if part else line.part_id}\": disponible "
                f"{available} de {quantity} solicitadas. Se guardó el cambio de todas formas — "
                "solicítalo a almacén cuando haya existencia."
            )

        line.quantity = quantity
        line.cost_total = cost
        line.unit_price, line.line_total = price_parts_cost(cost, quantity, order.discount_label)
        line.allocations = [
            ServiceOrderTransferLotAllocation(
                lot_id=lot.id, quantity=take, unit_cost=lot.unit_cost, warehouse_id=lot.warehouse_id
            )
            for lot, take in allocations
        ]
        await self.db.commit()
        await self.db.refresh(transfer)
        transfer.stock_warnings = [warning] if warning else []
        return transfer

    async def remove_transfer_line(self, line_id: uuid.UUID) -> ServiceOrderTransfer:
        """Drops a line entirely — e.g. the client is bringing every unit
        themselves, so nothing needs to be requested from almacén at all.
        Only while the ODT is still Pendiente, same as set_transfer_line_quantity."""
        line = await self.db.get(ServiceOrderTransferLine, line_id)
        if line is None:
            raise TransferNotFoundError(str(line_id))
        transfer = await self.db.get(ServiceOrderTransfer, line.transfer_id)
        await require_editable_order(self.db, transfer.service_order_id)
        if transfer.status != TransferStatus.PENDIENTE:
            raise TransferLineNotEditableError()

        await self.db.delete(line)
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

        await require_editable_order(self.db, transfer.service_order_id)
        await self.db.refresh(transfer, attribute_names=["status"])
        if transfer.status == TransferStatus.PENDIENTE:
            order = await self.get_order(transfer.service_order_id)
            for line in transfer.lines:
                part = await self.db.get(Part, line.part_id)
                if part is None:
                    continue
                # Dispatch consumes real FIFO lots (oldest first, across every
                # warehouse in the filial — ODTs have never been scoped to a
                # single warehouse) so the part can later be traced to a lot,
                # a purchase order, and a supplier. Mirrors PartsService.create_sale.
                real_lots = list(
                    (
                        await self.db.execute(
                            select(PartLot)
                            .where(
                                PartLot.filial_id == order.filial_id,
                                PartLot.part_id == line.part_id,
                                PartLot.quantity_remaining > 0,
                            )
                            .order_by(PartLot.received_at, PartLot.id)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                # Net of whatever OTHER pending lines still have reserved —
                # this line's own reservation is excluded, so as long as
                # nothing else touched inventory since it was previewed, this
                # lands on the exact same lots at the exact same price. If
                # its reservation lapsed (or a rare concurrent-preview race
                # let two lines reserve the same units), this simply falls
                # back to whatever's actually available now, same as before
                # this line-level reservation existed.
                reserved = await self._reserved_quantity_by_lot(line.part_id, exclude_line_id=line.id)
                real_lots_by_id = {lot.id: lot for lot in real_lots}
                available_lots = [
                    _AvailableLot(
                        id=lot.id,
                        unit_cost=lot.unit_cost,
                        warehouse_id=lot.warehouse_id,
                        quantity_remaining=max(0, lot.quantity_remaining - reserved.get(lot.id, 0)),
                    )
                    for lot in real_lots
                ]
                allocations = allocate_fifo(available_lots, line.quantity, part_id=part.id, part_name=part.name)
                # Replace the add-time preview with the real, final
                # consumption — inventory may have shifted since the line
                # was added (another order could have taken the cheaper
                # lot first), so re-derive the line's price from what was
                # actually dispatched, not what was previewed.
                cost = sum((Decimal(str(lot.unit_cost)) * take for lot, take in allocations), Decimal(0))
                line.allocations = [
                    ServiceOrderTransferLotAllocation(
                        lot_id=lot.id, quantity=take, unit_cost=lot.unit_cost, warehouse_id=lot.warehouse_id
                    )
                    for lot, take in allocations
                ]
                line.cost_total = cost
                line.unit_price, line.line_total = price_parts_cost(cost, line.quantity, order.discount_label)
                for lot, take in allocations:
                    real_lot = real_lots_by_id[lot.id]
                    real_lot.quantity_remaining -= take
                    self.db.add(
                        StockMovement(
                            filial_id=order.filial_id,
                            warehouse_id=lot.warehouse_id,
                            part_id=line.part_id,
                            movement_type=MovementType.SALIDA,
                            quantity=take,
                            unit_cost=lot.unit_cost,
                            reference=transfer.code,
                            responsible_user_id=fulfilled_by_user_id,
                        )
                    )
                part.stock_quantity = max(0, part.stock_quantity - line.quantity)
                _sync_availability(part)
            transfer.status = TransferStatus.PEDIDO
            transfer.fulfilled_by_user_id = fulfilled_by_user_id
            transfer.fulfilled_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(transfer)
        return transfer

    async def complete_transfer(
        self, transfer_id: uuid.UUID, completed_by_user_id: uuid.UUID | None = None
    ) -> ServiceOrderTransfer:
        """Confirms almacén physically handed the parts over — from
        Pedido only, never automatic. Pauses the elapsed-time counter
        running since fulfilled_at (see AlmacenService.list_service_order_requests)."""
        transfer = await self.db.get(ServiceOrderTransfer, transfer_id)
        if transfer is None:
            raise TransferNotFoundError(str(transfer_id))
        if transfer.status != TransferStatus.PEDIDO:
            raise InvalidTransferStatusTransitionError(transfer.status.value, TransferStatus.COMPLETADO.value)

        transfer.status = TransferStatus.COMPLETADO
        transfer.completed_by_user_id = completed_by_user_id
        transfer.completed_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(transfer)
        return transfer

    async def _get_or_create_pending_transfer(
        self, service_order_id: uuid.UUID
    ) -> ServiceOrderTransfer:
        await require_editable_order(self.db, service_order_id)
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
        self,
        transfer: ServiceOrderTransfer,
        part_id: uuid.UUID,
        quantity: int,
        payer: ServiceOrderPayer = ServiceOrderPayer.CLIENTE,
        service_order_task_id: uuid.UUID | None = None,
    ) -> str | None:
        """Adds (or tops up) a line on the ODT. Never blocked by stock — a
        client may bring their own part, or the shop may request it from
        another branch later; the only point that actually blocks on stock
        is dispatch ("pedir a almacén", mark_transfer_ordered). Returns a
        warning message when the line's requested quantity exceeds what's
        currently on the shelf, or None when fully covered."""
        await require_editable_order(self.db, transfer.service_order_id)
        order = await self.get_order(transfer.service_order_id)
        # Query directly instead of touching transfer.lines — for a transfer that
        # was just created in this same call, that relationship isn't loaded yet
        # and accessing it triggers a lazy-load SQLAlchemy's async session can't
        # run implicitly (MissingGreenlet).
        # Matched on part_id, payer AND originating task — the same part
        # added under two different payers, or by two different tasks, must
        # stay separate lines rather than blending their cost/provenance.
        result = await self.db.execute(
            select(ServiceOrderTransferLine).where(
                ServiceOrderTransferLine.transfer_id == transfer.id,
                ServiceOrderTransferLine.part_id == part_id,
                ServiceOrderTransferLine.payer == payer,
                ServiceOrderTransferLine.service_order_task_id == service_order_task_id,
            )
        )
        existing = result.scalar_one_or_none()
        new_quantity = (existing.quantity if existing is not None else 0) + quantity

        # Price via real FIFO consumption (oldest lot first, filial-wide —
        # same scope mark_transfer_ordered uses at dispatch), exactly like
        # the counter-sale flow — never off a single "latest" lot. Net of
        # whatever other pending lines have already reserved (excluding this
        # same line, which never competes against its own reservation) —
        # this IS also a soft reservation: the specific units it lands on
        # are unavailable to any other line's preview for RESERVATION_TTL,
        # so the price doesn't drift before dispatch. Still no stock is
        # actually touched until dispatch.
        cost, allocations, shortfall = await self._fifo_preview_cost(
            order.filial_id, part_id, new_quantity, exclude_line_id=existing.id if existing else None
        )

        warning: str | None = None
        if shortfall > 0:
            part = await self.db.get(Part, part_id)
            available = new_quantity - shortfall
            warning = (
                f"Stock insuficiente para \"{part.name if part else part_id}\": disponible "
                f"{available} de {new_quantity} solicitadas. Se agregó la línea de todas formas — "
                "solicítalo a almacén cuando haya existencia."
            )

        if existing is None:
            existing = ServiceOrderTransferLine(
                transfer_id=transfer.id,
                part_id=part_id,
                payer=payer,
                service_order_task_id=service_order_task_id,
            )
            self.db.add(existing)
        existing.quantity = new_quantity
        existing.cost_total = cost
        existing.unit_price, existing.line_total = price_parts_cost(cost, new_quantity, order.discount_label)
        existing.allocations = [
            ServiceOrderTransferLotAllocation(
                lot_id=lot.id, quantity=take, unit_cost=lot.unit_cost, warehouse_id=lot.warehouse_id
            )
            for lot, take in allocations
        ]
        # Subsequent additions of this part in the same task must see this line.
        await self.db.flush()
        return warning

    async def _reserved_quantity_by_lot(
        self, part_id: uuid.UUID, exclude_line_id: uuid.UUID | None
    ) -> dict[uuid.UUID, int]:
        """How much of each of this part's lots is currently claimed by
        OTHER pending ODT lines' still-fresh (< RESERVATION_TTL old) price
        preview — so a second line can't preview or dispatch against the
        exact same units before the first one actually consumes them. A
        lapsed reservation just stops counting here; nothing needs to
        actively clear it. Once a transfer is PEDIDO, its lines' allocations
        are real consumption already reflected in PartLot.quantity_remaining
        — counting them again here would double-subtract, so only PENDIENTE
        transfers are considered."""
        cutoff = datetime.now(UTC) - RESERVATION_TTL
        query = (
            select(ServiceOrderTransferLotAllocation.lot_id, func.sum(ServiceOrderTransferLotAllocation.quantity))
            .join(PartLot, PartLot.id == ServiceOrderTransferLotAllocation.lot_id)
            .join(
                ServiceOrderTransferLine,
                ServiceOrderTransferLine.id == ServiceOrderTransferLotAllocation.transfer_line_id,
            )
            .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
            .where(
                PartLot.part_id == part_id,
                ServiceOrderTransferLotAllocation.created_at >= cutoff,
                ServiceOrderTransfer.status == TransferStatus.PENDIENTE,
            )
            .group_by(ServiceOrderTransferLotAllocation.lot_id)
        )
        if exclude_line_id is not None:
            query = query.where(ServiceOrderTransferLotAllocation.transfer_line_id != exclude_line_id)
        result = await self.db.execute(query)
        return dict(result.all())

    async def _fifo_preview_cost(
        self,
        filial_id: uuid.UUID,
        part_id: uuid.UUID,
        quantity: int,
        exclude_line_id: uuid.UUID | None = None,
    ) -> tuple[Decimal, list[tuple[_AvailableLot, int]], int]:
        """Total cost FIFO would actually charge for this quantity — oldest
        lots first, filial-wide, weighted across every lot it spans, net of
        whatever other pending lines have already reserved (see
        _reserved_quantity_by_lot) — with any shortfall (the shelf can't
        fully cover it) priced at the last known cost as a best-effort
        estimate. Shared by the ODT add-time preview and the Upsell proposal
        amount, so neither prices a quantity spanning more than one cost
        layer off a single lot's cost. Returns (total_cost, allocations,
        shortfall) — a read-only preview, no stock is touched here."""
        lots = list(
            (
                await self.db.execute(
                    select(PartLot)
                    .where(
                        PartLot.filial_id == filial_id,
                        PartLot.part_id == part_id,
                        PartLot.quantity_remaining > 0,
                    )
                    .order_by(PartLot.received_at, PartLot.id)
                )
            ).scalars()
        )
        reserved = await self._reserved_quantity_by_lot(part_id, exclude_line_id)
        available_lots = [
            _AvailableLot(
                id=lot.id,
                unit_cost=lot.unit_cost,
                warehouse_id=lot.warehouse_id,
                quantity_remaining=max(0, lot.quantity_remaining - reserved.get(lot.id, 0)),
            )
            for lot in lots
        ]
        available_lots = [lot for lot in available_lots if lot.quantity_remaining > 0]
        allocations, shortfall = allocate_fifo_preview(available_lots, quantity)
        cost = sum((Decimal(str(lot.unit_cost)) * take for lot, take in allocations), Decimal(0))
        if shortfall > 0:
            fallback_cost = (
                allocations[-1][0].unit_cost if allocations else await self._last_known_unit_cost(part_id)
            )
            cost += Decimal(str(fallback_cost)) * shortfall
        return cost, allocations, shortfall

    async def _last_known_unit_cost(self, part_id: uuid.UUID) -> Decimal:
        """The most recent cost this part was ever received at, regardless of
        whether that lot still has stock remaining — used only to price the
        shortfall portion of an add-time preview, never to block it."""
        result = await self.db.execute(
            select(PartLot.unit_cost)
            .where(PartLot.part_id == part_id)
            .order_by(PartLot.received_at.desc(), PartLot.id.desc())
            .limit(1)
        )
        cost = result.scalar_one_or_none()
        return Decimal(str(cost)) if cost is not None else Decimal(0)

    # Pricing summary

    async def get_order_summary(self, service_order_id: uuid.UUID) -> OrderSummary:
        order = await self.get_order(service_order_id)
        if order.invoiced_at is not None or order.status == ServiceOrderStatus.ORDEN_CERRADA:
            if order.pricing_snapshot is not None:
                return OrderSummary.model_validate(order.pricing_snapshot)
            # Older invoices stored only the final amount. Never reconstruct their
            # breakdown using current labor/tax rates or potentially edited ODTs.
            return OrderSummary(
                discount_label=order.discount_label,
                tasks=[
                    TaskRead.model_validate(task, from_attributes=True)
                    for task in await self.list_tasks(service_order_id)
                ],
                transfers=[
                    TransferRead(
                        id=tr.id,
                        code=f"{order.code}-{tr.code}",
                        status=tr.status,
                        created_at=tr.created_at,
                        subtotal=None,
                        fulfilled_at=tr.fulfilled_at,
                        fulfilled_by_user_id=tr.fulfilled_by_user_id,
                        lines=[
                            TransferLineRead(
                                id=line.id,
                                part_id=line.part_id,
                                quantity=line.quantity,
                                unit_price=None,
                                subtotal=None,
                                payer=line.payer,
                            )
                            for line in tr.lines
                        ],
                    )
                    for tr in await self.list_transfers(service_order_id)
                ],
                parts_subtotal=None,
                labor_subtotal=None,
                non_client_subtotal=0,
                iva_percentage=None,
                iva_amount=None,
                total=float(order.total_amount) if order.total_amount is not None else 0.0,
                pricing_frozen=True,
                pricing_snapshot_available=False,
            )
        return await self._build_order_summary(order)

    async def _build_order_summary(self, order: ServiceOrder) -> OrderSummary:
        service_order_id = order.id
        tasks = await self.list_tasks(service_order_id)
        transfers = await self.list_transfers(service_order_id)

        settings_result = await self.db.execute(
            select(LaborSettings).where(LaborSettings.filial_id == order.filial_id)
        )
        settings = settings_result.scalar_one_or_none()
        hourly_rate = float(settings.hourly_rate) if settings else 25.0
        iva_percentage = float(settings.iva_percentage) if settings else 16.0

        all_lines = [line for tr in transfers for line in tr.lines]

        labor_subtotal = (
            sum(float(t.hours_snapshot) for t in tasks if t.payer == ServiceOrderPayer.CLIENTE) * hourly_rate
        )
        non_client_labor = (
            sum(float(t.hours_snapshot) for t in tasks if t.payer != ServiceOrderPayer.CLIENTE) * hourly_rate
        )
        parts_subtotal = sum(
            float(line.line_total) for line in all_lines if line.payer == ServiceOrderPayer.CLIENTE
        )
        non_client_parts = sum(
            float(line.line_total) for line in all_lines if line.payer != ServiceOrderPayer.CLIENTE
        )
        non_client_subtotal = non_client_labor + non_client_parts
        iva_amount = (labor_subtotal + parts_subtotal) * iva_percentage / 100
        total = labor_subtotal + parts_subtotal + iva_amount

        payer_breakdown = []
        for payer in ServiceOrderPayer:
            labor = sum(float(t.hours_snapshot) for t in tasks if t.payer == payer) * hourly_rate
            parts = sum(float(line.line_total) for line in all_lines if line.payer == payer)
            payer_breakdown.append({
                "payer": payer,
                "labor_subtotal": labor,
                "parts_subtotal": parts,
                "subtotal": labor + parts,
            })

        return OrderSummary(
            payer_breakdown=payer_breakdown,
            discount_label=order.discount_label,
            tasks=[
                TaskRead(
                    id=t.id,
                    tempario_id=t.tempario_id,
                    code_snapshot=t.code_snapshot,
                    name_snapshot=t.name_snapshot,
                    hours_snapshot=float(t.hours_snapshot),
                    status=t.status,
                    payer=t.payer,
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
                            subtotal=float(line.line_total),
                            payer=line.payer,
                        )
                        for line in tr.lines
                    ],
                    subtotal=sum(float(line.line_total) for line in tr.lines),
                    created_at=tr.created_at,
                )
                for tr in transfers
            ],
            parts_subtotal=parts_subtotal,
            labor_subtotal=labor_subtotal,
            non_client_subtotal=non_client_subtotal,
            iva_percentage=iva_percentage,
            iva_amount=iva_amount,
            total=total,
        )

    # Upsells

    async def _get_upsell_model(self, upsell_id: uuid.UUID) -> Upsell:
        upsell = await self.db.get(Upsell, upsell_id)
        if upsell is None:
            raise UpsellNotFoundError(str(upsell_id))
        return upsell

    async def _hourly_rate(self, filial_id: uuid.UUID) -> float:
        result = await self.db.execute(select(LaborSettings).where(LaborSettings.filial_id == filial_id))
        settings = result.scalar_one_or_none()
        return float(settings.hourly_rate) if settings else 25.0

    def _upsell_to_read(self, upsell: Upsell, hourly_rate: float, discount_label: str) -> UpsellRead:
        multiplier = float(PARTS_MULTIPLIERS[discount_label])
        labor_cost = sum(float(t.hours_snapshot) for t in upsell.tasks) * hourly_rate
        parts = [
            UpsellPartRead(
                id=p.id,
                part_id=p.part_id,
                name_snapshot=p.name_snapshot,
                quantity=p.quantity,
                unit_cost_snapshot=float(p.unit_cost_snapshot),
                line_total=p.quantity * float(p.unit_cost_snapshot) * multiplier,
            )
            for p in upsell.parts
        ]
        return UpsellRead(
            id=upsell.id,
            service_order_id=upsell.service_order_id,
            title=upsell.title,
            description=upsell.description,
            detected_by_user_id=upsell.detected_by_user_id,
            evidence_count=upsell.evidence_count,
            status=upsell.status,
            tasks=[
                UpsellTaskRead(
                    id=t.id,
                    tempario_id=t.tempario_id,
                    code_snapshot=t.code_snapshot,
                    name_snapshot=t.name_snapshot,
                    hours_snapshot=float(t.hours_snapshot),
                )
                for t in upsell.tasks
            ],
            parts=parts,
            amount=labor_cost + sum(p.line_total for p in parts),
            approved_by_user_id=upsell.approved_by_user_id,
            approval_channel=upsell.approval_channel,
            created_at=upsell.created_at,
            resolved_at=upsell.resolved_at,
        )

    async def list_upsells(self, filial_id: uuid.UUID) -> list[UpsellRead]:
        """All upsells across every order in the filial — this is a filial-wide
        list, not scoped to a single ODS."""
        result = await self.db.execute(
            select(Upsell, ServiceOrder.discount_label)
            .join(ServiceOrder, ServiceOrder.id == Upsell.service_order_id)
            .where(ServiceOrder.filial_id == filial_id)
            .order_by(Upsell.created_at.desc())
        )
        rows = result.all()
        hourly_rate = await self._hourly_rate(filial_id)
        return [self._upsell_to_read(upsell, hourly_rate, discount_label) for upsell, discount_label in rows]

    async def get_upsell(self, upsell_id: uuid.UUID) -> UpsellRead:
        upsell = await self._get_upsell_model(upsell_id)
        order = await self.get_order(upsell.service_order_id)
        hourly_rate = await self._hourly_rate(order.filial_id)
        return self._upsell_to_read(upsell, hourly_rate, order.discount_label)

    async def create_upsell(self, service_order_id: uuid.UUID, payload: UpsellCreate) -> UpsellRead:
        order = await require_editable_order(self.db, service_order_id)
        upsell = Upsell(
            service_order_id=service_order_id,
            title=payload.title,
            description=payload.description,
            evidence_count=payload.evidence_count,
            detected_by_user_id=payload.detected_by_user_id,
        )
        self.db.add(upsell)
        await self.db.flush()

        for task_input in payload.tasks:
            tempario = await self.db.get(Tempario, task_input.tempario_id)
            if tempario is None or tempario.filial_id != order.filial_id:
                raise TaskNotFoundError(str(task_input.tempario_id))
            self.db.add(
                UpsellTask(
                    upsell_id=upsell.id,
                    tempario_id=tempario.id,
                    code_snapshot=tempario.code,
                    name_snapshot=tempario.name,
                    hours_snapshot=tempario.estimated_hours,
                )
            )
        for part_input in payload.parts:
            part = await self.db.get(Part, part_input.part_id)
            if part is None or part.filial_id != order.filial_id:
                raise TransferNotFoundError(str(part_input.part_id))
            # Weighted average across every FIFO lot this quantity would
            # actually draw from — a proposed quantity spanning more than
            # one cost layer must not be priced off a single lot's cost,
            # same reasoning as the ODT add-time preview.
            total_cost, _allocations, _shortfall = await self._fifo_preview_cost(
                order.filial_id, part.id, part_input.quantity
            )
            unit_cost = total_cost / part_input.quantity if part_input.quantity else Decimal(0)
            self.db.add(
                UpsellPart(
                    upsell_id=upsell.id,
                    part_id=part.id,
                    name_snapshot=part.name,
                    quantity=part_input.quantity,
                    unit_cost_snapshot=unit_cost,
                )
            )

        await self.db.commit()
        await self.db.refresh(upsell)
        return await self.get_upsell(upsell.id)

    async def decide_upsell(
        self, upsell_id: uuid.UUID, payload: UpsellDecisionInput, decided_by_user_id: uuid.UUID | None
    ) -> UpsellRead:
        upsell = await self._get_upsell_model(upsell_id)
        await require_editable_order(self.db, upsell.service_order_id)
        status = UpsellStatus(payload.status)

        if status == UpsellStatus.APROBADO:
            # Approving doesn't replay the upsell's own preview snapshots —
            # it adds the same tasks/parts fresh, exactly as if an advisor
            # had added them by hand right now, so they price off today's
            # tempario/labor rate/FIFO cost, not whatever they were when
            # the upsell was first proposed.
            for task in upsell.tasks:
                await self.add_task(upsell.service_order_id, task.tempario_id, payer=ServiceOrderPayer.CLIENTE)
            for part in upsell.parts:
                await self.add_transfer_line(
                    upsell.service_order_id, part.part_id, part.quantity, payer=ServiceOrderPayer.CLIENTE
                )
            upsell.approved_by_user_id = decided_by_user_id
            upsell.approval_channel = payload.approval_channel

        upsell.status = status
        upsell.resolved_at = datetime.now(UTC)
        await self.db.commit()
        return await self.get_upsell(upsell.id)

    # Warranty claims (unified "reclamo de garantía") — covers all four
    # responsible-party cases: factory/importer, a shop-assumed comeback, a
    # supplier-assumed defective part, or a manufacturer campaign/recall.
    # Creating a claim never touches an existing order; converting one does.

    async def _warranty_claim_to_read(self, claim: WarrantyClaim, vehicle: Vehicle) -> WarrantyClaimRead:
        tempario_name = None
        if claim.tempario_id is not None:
            tempario = await self.db.get(Tempario, claim.tempario_id)
            tempario_name = tempario.name if tempario else None
        part_name = None
        if claim.part_id is not None:
            part = await self.db.get(Part, claim.part_id)
            part_name = part.name if part else None

        service_order_code = None
        if claim.service_order_id is not None:
            order = await self.db.get(ServiceOrder, claim.service_order_id)
            service_order_code = order.code if order else None

        auto_claim_ids: list[uuid.UUID] = []
        supplier_claim_note: str | None = None
        if (
            claim.status == WarrantyClaimStatus.CONVERTIDO_A_ODS
            and claim.claim_type == WarrantyClaimType.REPUESTO_PROVEEDOR
            and claim.part_id is not None
        ):
            result = await self.db.execute(
                select(SupplierClaim.id).where(SupplierClaim.warranty_claim_id == claim.id)
            )
            auto_claim_ids = [row[0] for row in result.all()]
            if not auto_claim_ids:
                if claim.service_order_id is None:
                    supplier_claim_note = (
                        "No se pudo generar el reclamo al proveedor: este reclamo no tiene una orden de "
                        "servicio de origen de la que trazar el despacho."
                    )
                else:
                    # Same "actually dispatched" scoping as _auto_claim_defective_part
                    # — a pre-dispatch price-preview allocation doesn't count as
                    # "hay un despacho registrado."
                    has_allocation = (
                        await self.db.execute(
                            select(ServiceOrderTransferLotAllocation.id)
                            .join(
                                ServiceOrderTransferLine,
                                ServiceOrderTransferLine.id == ServiceOrderTransferLotAllocation.transfer_line_id,
                            )
                            .join(
                                ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id
                            )
                            .where(
                                ServiceOrderTransfer.service_order_id == claim.service_order_id,
                                ServiceOrderTransferLine.part_id == claim.part_id,
                                # PEDIDO or COMPLETADO — both mean it was actually
                                # dispatched; COMPLETADO is just a later confirmation
                                # of the same allocation, not a different one.
                                ServiceOrderTransfer.status.in_(
                                    [TransferStatus.PEDIDO, TransferStatus.COMPLETADO]
                                ),
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if has_allocation is None:
                        supplier_claim_note = (
                            "No se pudo generar el reclamo al proveedor: no hay un despacho de este repuesto "
                            "registrado en la orden."
                        )
                    else:
                        supplier_claim_note = (
                            "No se pudo generar el reclamo al proveedor: el lote de origen no tiene una orden "
                            "de compra asociada."
                        )

        resulting_service_order_code = None
        if claim.resulting_service_order_id is not None:
            resulting_order = await self.db.get(ServiceOrder, claim.resulting_service_order_id)
            resulting_service_order_code = resulting_order.code if resulting_order else None

        return WarrantyClaimRead(
            id=claim.id,
            code=claim.code,
            filial_id=claim.filial_id,
            claim_type=claim.claim_type,
            vehicle_id=claim.vehicle_id,
            vehicle_plate=vehicle.plate,
            vehicle_vin=vehicle.vin,
            client_name=vehicle.client.full_name,
            service_order_id=claim.service_order_id,
            service_order_code=service_order_code,
            tempario_id=claim.tempario_id,
            tempario_name=tempario_name,
            part_id=claim.part_id,
            part_name=part_name,
            failure_category=claim.failure_category,
            failure_cause=claim.failure_cause,
            reported_symptom=claim.reported_symptom,
            reported_mileage=claim.reported_mileage,
            vehicle_mileage_at_claim=claim.vehicle_mileage_at_claim,
            mileage_inconsistent=claim.mileage_inconsistent,
            claimed_at=claim.claimed_at,
            recorded_by_user_id=claim.recorded_by_user_id,
            note=claim.note,
            photo_urls=list(claim.photo_urls or []),
            document_urls=list(claim.document_urls or []),
            created_at=claim.created_at,
            status=claim.status,
            auto_generated_supplier_claim_ids=auto_claim_ids,
            supplier_claim_note=supplier_claim_note,
            authorized_by_user_id=claim.authorized_by_user_id,
            authorized_at=claim.authorized_at,
            warranty_override=claim.warranty_override,
            warranty_override_note=claim.warranty_override_note,
            converted_by_user_id=claim.converted_by_user_id,
            converted_at=claim.converted_at,
            resulting_service_order_id=claim.resulting_service_order_id,
            resulting_service_order_code=resulting_service_order_code,
        )

    async def _auto_claim_defective_part(self, order: ServiceOrder, claim: WarrantyClaim) -> None:
        # Only allocations from an actually-dispatched ODT count as real
        # consumption — a line's allocation is set the moment it's added
        # (a FIFO price preview, before any stock moves) and again,
        # authoritatively, at dispatch. Claiming against a preview would
        # blame a supplier for a part that may never even ship from that lot.
        rows = (
            await self.db.execute(
                select(ServiceOrderTransferLotAllocation, PartLot)
                .join(PartLot, PartLot.id == ServiceOrderTransferLotAllocation.lot_id)
                .join(
                    ServiceOrderTransferLine,
                    ServiceOrderTransferLine.id == ServiceOrderTransferLotAllocation.transfer_line_id,
                )
                .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
                .where(
                    ServiceOrderTransfer.service_order_id == order.id,
                    ServiceOrderTransferLine.part_id == claim.part_id,
                    # PEDIDO or COMPLETADO — both mean it was actually
                    # dispatched; COMPLETADO is just a later confirmation of
                    # the same allocation, not a different one.
                    ServiceOrderTransfer.status.in_([TransferStatus.PEDIDO, TransferStatus.COMPLETADO]),
                )
            )
        ).all()

        quantity_by_lot: dict[uuid.UUID, int] = {}
        lot_by_id: dict[uuid.UUID, PartLot] = {}
        for allocation, lot in rows:
            quantity_by_lot[lot.id] = quantity_by_lot.get(lot.id, 0) + allocation.quantity
            lot_by_id[lot.id] = lot
        if not quantity_by_lot:
            return

        from app.modules.administracion.schemas import SupplierClaimCreate
        from app.modules.administracion.service import AdministracionService

        admin_service = AdministracionService(self.db)
        for lot_id, quantity in quantity_by_lot.items():
            lot = lot_by_id[lot_id]
            if lot.purchase_request_id is None:
                continue
            purchase_request = await self.db.get(PurchaseRequest, lot.purchase_request_id)
            if purchase_request is None:
                continue
            await admin_service.create_claim(
                SupplierClaimCreate(
                    filial_id=order.filial_id,
                    part_id=claim.part_id,
                    quantity=quantity,
                    supplier_id=purchase_request.supplier_id,
                    note=f"Generado automáticamente — reclamo {claim.code}, repuesto defectuoso",
                    lot_id=lot.id,
                    purchase_request_id=purchase_request.id,
                    warranty_claim_id=claim.id,
                )
            )

    async def _get_order_invoice(self, order_id: uuid.UUID) -> ServiceOrderInvoice:
        result = await self.db.execute(
            select(ServiceOrderInvoice).where(ServiceOrderInvoice.service_order_id == order_id)
        )
        invoice = result.scalar_one_or_none()
        if invoice is None:
            raise OrderNotInvoicedError()
        return invoice

    async def _next_claim_sequence_number(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(WarrantyClaim.sequence_number)).where(WarrantyClaim.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 100) + 1

    async def _check_duplicate_open_claim(
        self, vehicle_id: uuid.UUID, tempario_id: uuid.UUID | None, part_id: uuid.UUID | None
    ) -> WarrantyClaim | None:
        """Non-blocking: a claim already open (solicitado/autorizado) for the
        same vehicle and the same 'componente' (task or part) — surfaced as
        a warning, never a hard stop."""
        if tempario_id is None and part_id is None:
            return None
        conditions = []
        if tempario_id is not None:
            conditions.append(WarrantyClaim.tempario_id == tempario_id)
        if part_id is not None:
            conditions.append(WarrantyClaim.part_id == part_id)
        result = await self.db.execute(
            select(WarrantyClaim)
            .where(
                WarrantyClaim.vehicle_id == vehicle_id,
                WarrantyClaim.status.in_([WarrantyClaimStatus.SOLICITADO, WarrantyClaimStatus.AUTORIZADO]),
                *conditions,
            )
            .order_by(WarrantyClaim.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_claim_context(
        self,
        vehicle_id: uuid.UUID,
        service_order_id: uuid.UUID | None = None,
        tempario_id: uuid.UUID | None = None,
        part_id: uuid.UUID | None = None,
    ) -> WarrantyClaimContext:
        """Everything an advisor needs to verify what the system claims about
        a vehicle before filing a warranty claim — last visit, current
        mileage, factory/workshop coverage validity, and any already-open
        duplicate claim — instead of a bare pass/fail message."""
        from app.modules.clients.service import ClientService
        from app.modules.post_ventas.exceptions import VehicleWarrantyNotFoundError
        from app.modules.post_ventas.service import PostVentasService

        vehicle = await self._get_vehicle_with_client(vehicle_id)
        filial_id = vehicle.client.filial_id

        current_mileage, last_visit_date, last_visit_order_id = await ClientService(
            self.db
        ).get_mileage_context(vehicle_id)
        last_visit_service_order_code = None
        if last_visit_order_id is not None:
            last_order = await self.db.get(ServiceOrder, last_visit_order_id)
            last_visit_service_order_code = last_order.code if last_order else None

        factory_warranty = None
        workshop_warranties: list = []
        if vehicle.vin:
            post_ventas = PostVentasService(self.db)
            try:
                factory_warranty = await post_ventas.get_vehicle_warranty_by_vin(filial_id, vehicle.vin)
            except VehicleWarrantyNotFoundError:
                factory_warranty = None
            workshop_warranties = await post_ventas.list_workshop_warranties_by_vin(filial_id, vehicle.vin)
            if service_order_id is not None:
                workshop_warranties = [w for w in workshop_warranties if w.service_order_id == service_order_id]

        duplicate = await self._check_duplicate_open_claim(vehicle_id, tempario_id, part_id)
        duplicate_read = await self._warranty_claim_to_read(duplicate, vehicle) if duplicate else None

        return WarrantyClaimContext(
            current_mileage=current_mileage,
            last_visit_date=last_visit_date,
            last_visit_service_order_code=last_visit_service_order_code,
            factory_warranty=factory_warranty,
            workshop_warranties=workshop_warranties,
            duplicate_open_claim=duplicate_read,
        )

    async def create_warranty_claim(
        self,
        payload: WarrantyClaimCreate,
        photo_urls: list[str],
        document_urls: list[str],
        recorded_by_user_id: uuid.UUID | None,
    ) -> WarrantyClaimRead:
        vehicle = await self._get_vehicle_with_client(payload.vehicle_id)
        filial_id = vehicle.client.filial_id

        if payload.claim_type == WarrantyClaimType.COMEBACK and payload.service_order_id is None:
            raise ServiceOrderRequiredForComebackError()

        if payload.service_order_id is not None:
            await self._get_order_invoice(payload.service_order_id)
            if payload.tempario_id is not None:
                task = (
                    await self.db.execute(
                        select(ServiceOrderTask).where(
                            ServiceOrderTask.service_order_id == payload.service_order_id,
                            ServiceOrderTask.tempario_id == payload.tempario_id,
                        )
                    )
                ).scalar_one_or_none()
                if task is None:
                    raise WarrantyClaimReferenceMismatchError()
            if payload.part_id is not None:
                line = (
                    await self.db.execute(
                        select(ServiceOrderTransferLine)
                        .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
                        .where(
                            ServiceOrderTransfer.service_order_id == payload.service_order_id,
                            ServiceOrderTransferLine.part_id == payload.part_id,
                        )
                    )
                ).scalar_one_or_none()
                if line is None:
                    raise WarrantyClaimReferenceMismatchError()

        from app.modules.clients.service import ClientService

        current_mileage = await ClientService(self.db).get_current_mileage(payload.vehicle_id)
        mileage_inconsistent = current_mileage is not None and payload.reported_mileage < current_mileage

        sequence_number = await self._next_claim_sequence_number(filial_id)
        claim = WarrantyClaim(
            filial_id=filial_id,
            sequence_number=sequence_number,
            claim_type=payload.claim_type,
            vehicle_id=payload.vehicle_id,
            service_order_id=payload.service_order_id,
            tempario_id=payload.tempario_id,
            part_id=payload.part_id,
            failure_category=payload.failure_category,
            failure_cause=payload.failure_cause,
            reported_symptom=payload.reported_symptom,
            reported_mileage=payload.reported_mileage,
            vehicle_mileage_at_claim=current_mileage,
            mileage_inconsistent=mileage_inconsistent,
            claimed_at=payload.claimed_at,
            recorded_by_user_id=recorded_by_user_id,
            note=payload.note,
            photo_urls=photo_urls,
            document_urls=document_urls,
        )
        self.db.add(claim)
        await self.db.commit()
        await self.db.refresh(claim)
        return await self._warranty_claim_to_read(claim, vehicle)

    async def authorize_warranty_claim(
        self, claim_id: uuid.UUID, payload: WarrantyClaimAuthorizationInput, authorized_by_user_id: uuid.UUID | None
    ) -> WarrantyClaimRead:
        claim = await self.db.get(WarrantyClaim, claim_id)
        if claim is None:
            raise WarrantyClaimNotFoundError(str(claim_id))
        if claim.status != WarrantyClaimStatus.SOLICITADO:
            raise WarrantyClaimAlreadyDecidedError()

        vehicle = await self._get_vehicle_with_client(claim.vehicle_id)

        if payload.decision == "aprobado":
            if claim.claim_type in (WarrantyClaimType.FABRICA, WarrantyClaimType.CAMPANA_RECALL):
                from app.modules.post_ventas.exceptions import VehicleWarrantyNotFoundError
                from app.modules.post_ventas.service import PostVentasService

                has_vigente_warranty = False
                if vehicle.vin:
                    try:
                        warranty = await PostVentasService(self.db).get_vehicle_warranty_by_vin(
                            claim.filial_id, vehicle.vin
                        )
                        has_vigente_warranty = warranty.status == "vigente"
                    except VehicleWarrantyNotFoundError:
                        has_vigente_warranty = False

                if not has_vigente_warranty:
                    if not payload.warranty_override:
                        raise VehicleWarrantyRequiredError()
                    if not payload.warranty_override_note:
                        raise WarrantyOverrideNoteRequiredError()
                claim.warranty_override = payload.warranty_override
                claim.warranty_override_note = payload.warranty_override_note
            else:
                if payload.failure_category is not None:
                    claim.failure_category = payload.failure_category
                if claim.failure_category is None:
                    raise FailureCategoryRequiredError()

            claim.status = WarrantyClaimStatus.AUTORIZADO
        else:
            claim.status = WarrantyClaimStatus.RECHAZADO

        claim.authorized_by_user_id = authorized_by_user_id
        claim.authorized_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(claim)
        return await self._warranty_claim_to_read(claim, vehicle)

    async def convert_warranty_claim_to_order(
        self, claim_id: uuid.UUID, payload: WarrantyClaimConvertInput, converted_by_user_id: uuid.UUID | None
    ) -> WarrantyClaimRead:
        claim = await self.db.get(WarrantyClaim, claim_id)
        if claim is None:
            raise WarrantyClaimNotFoundError(str(claim_id))
        if claim.status == WarrantyClaimStatus.CONVERTIDO_A_ODS:
            raise WarrantyClaimAlreadyConvertedError()
        if claim.status != WarrantyClaimStatus.AUTORIZADO:
            raise WarrantyClaimNotAuthorizedError()

        vehicle = await self._get_vehicle_with_client(claim.vehicle_id)
        original_order = await self.get_order(claim.service_order_id) if claim.service_order_id else None

        payer_by_type = {
            WarrantyClaimType.FABRICA: ServiceOrderPayer.GARANTIA_FABRICA,
            WarrantyClaimType.CAMPANA_RECALL: ServiceOrderPayer.GARANTIA_FABRICA,
            WarrantyClaimType.COMEBACK: ServiceOrderPayer.GARANTIA_TALLER,
            WarrantyClaimType.REPUESTO_PROVEEDOR: ServiceOrderPayer.PROVEEDOR,
        }
        payer = payer_by_type[claim.claim_type]

        next_seq = await self._next_sequence_number(claim.filial_id)
        intake_mileage = payload.intake_mileage
        if intake_mileage is None:
            intake_mileage = original_order.intake_mileage if original_order else claim.reported_mileage
        cause_label = claim.failure_cause or claim.reported_symptom or claim.claim_type.value
        new_order = ServiceOrder(
            filial_id=claim.filial_id,
            vehicle_id=claim.vehicle_id,
            order_type=ServiceOrderType.RETRABAJO,
            advisor_user_id=original_order.advisor_user_id if original_order else None,
            intake_mileage=intake_mileage,
            customer_reason=f"Retrabajo de garantía — {cause_label}",
            promised_at=payload.promised_at or datetime.now(UTC),
            sequence_number=next_seq,
        )
        self.db.add(new_order)
        await self.db.flush()

        if claim.tempario_id is not None:
            await self.add_task(new_order.id, claim.tempario_id, payer=payer)
        if claim.part_id is not None:
            quantity = 1
            if original_order is not None:
                quantity = await self._line_quantity_for_part(original_order.id, claim.part_id)
            await self.add_transfer_line(new_order.id, claim.part_id, quantity, payer=payer)

        claim.resulting_service_order_id = new_order.id
        claim.converted_by_user_id = converted_by_user_id
        claim.converted_at = datetime.now(UTC)
        claim.status = WarrantyClaimStatus.CONVERTIDO_A_ODS

        if claim.claim_type == WarrantyClaimType.REPUESTO_PROVEEDOR and claim.part_id is not None and original_order is not None:
            await self._auto_claim_defective_part(original_order, claim)

        await self.db.commit()
        await self.db.refresh(claim)
        return await self._warranty_claim_to_read(claim, vehicle)

    async def get_warranty_claim(self, claim_id: uuid.UUID) -> WarrantyClaimRead:
        claim = await self.db.get(WarrantyClaim, claim_id)
        if claim is None:
            raise WarrantyClaimNotFoundError(str(claim_id))
        vehicle = await self._get_vehicle_with_client(claim.vehicle_id)
        return await self._warranty_claim_to_read(claim, vehicle)

    async def list_warranty_claims(
        self,
        filial_id: uuid.UUID,
        status: WarrantyClaimStatus | None = None,
        vehicle_id: uuid.UUID | None = None,
        service_order_id: uuid.UUID | None = None,
    ) -> list[WarrantyClaimRead]:
        query = select(WarrantyClaim).where(WarrantyClaim.filial_id == filial_id)
        if status is not None:
            query = query.where(WarrantyClaim.status == status)
        if vehicle_id is not None:
            query = query.where(WarrantyClaim.vehicle_id == vehicle_id)
        if service_order_id is not None:
            query = query.where(WarrantyClaim.service_order_id == service_order_id)
        query = query.order_by(WarrantyClaim.created_at.desc())

        result = await self.db.execute(query)
        claims = list(result.scalars().all())
        vehicle_ids = {c.vehicle_id for c in claims}
        vehicles: dict[uuid.UUID, Vehicle] = {}
        if vehicle_ids:
            result = await self.db.execute(
                select(Vehicle).options(selectinload(Vehicle.client)).where(Vehicle.id.in_(vehicle_ids))
            )
            vehicles = {v.id: v for v in result.scalars()}
        return [await self._warranty_claim_to_read(c, vehicles[c.vehicle_id]) for c in claims]

    async def _line_quantity_for_part(self, order_id: uuid.UUID, part_id: uuid.UUID) -> int:
        """The quantity actually dispatched for this part on this order —
        reused from the same lookup _auto_claim_defective_part relies on —
        so the inherited transfer line on the new order matches reality
        instead of guessing a quantity of 1."""
        result = await self.db.execute(
            select(ServiceOrderTransferLine)
            .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
            .where(
                ServiceOrderTransfer.service_order_id == order_id,
                ServiceOrderTransferLine.part_id == part_id,
            )
        )
        lines = list(result.scalars().all())
        return sum(line.quantity for line in lines) or 1

    async def _get_vehicle_with_client(self, vehicle_id: uuid.UUID) -> Vehicle:
        from app.modules.clients.exceptions import VehicleNotFoundError

        result = await self.db.execute(
            select(Vehicle).options(selectinload(Vehicle.client)).where(Vehicle.id == vehicle_id)
        )
        vehicle = result.scalar_one_or_none()
        if vehicle is None:
            raise VehicleNotFoundError(str(vehicle_id))
        return vehicle

    async def get_vehicle_filial_id(self, vehicle_id: uuid.UUID) -> uuid.UUID:
        vehicle = await self._get_vehicle_with_client(vehicle_id)
        return vehicle.client.filial_id
