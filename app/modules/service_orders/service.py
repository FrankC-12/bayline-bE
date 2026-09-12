import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError
from app.modules.clients.models import Vehicle
from app.modules.parts.models import Part
from app.modules.parts.pricing import price_parts_cost
from app.modules.parts.service import _sync_availability
from app.modules.post_ventas.models import LaborSettings, Tempario, VehicleWarranty
from app.modules.service_orders.enums import (
    ReworkAuthorizationStatus,
    ReworkClaimStatus,
    ReworkFailureCategory,
    ServiceOrderPayer,
    ServiceOrderStatus,
    ServiceOrderType,
    TaskStatus,
    TransferStatus,
    UpsellStatus,
)
from app.modules.service_orders.exceptions import (
    BayNotFoundError,
    FailureCategoryRequiredError,
    InvalidStatusTransitionError,
    OrderNotInvoicedError,
    ReworkClaimAlreadyAuthorizedError,
    ReworkClaimAlreadyClosedError,
    ReworkClaimNotFoundError,
    ReworkClaimReferenceMismatchError,
    ServiceOrderNotFoundError,
    TaskNotFoundError,
    TransferNotFoundError,
    UpsellNotFoundError,
    VehicleWarrantyRequiredError,
    WarrantyClaimWarrantyMismatchError,
    WarrantyOverrideNoteRequiredError,
)
from app.modules.service_orders.guards import require_editable_order
from app.modules.service_orders.models import (
    Bay,
    ReworkClaim,
    ServiceOrder,
    ServiceOrderInvoice,
    ServiceOrderTask,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    ServiceOrderTransferLotAllocation,
    Upsell,
    WarrantyClaim,
    WarrantyClaimWarranty,
)
from app.modules.service_orders.schemas import (
    BayCreate,
    BayUpdate,
    OrderSummary,
    ReworkClaimAuthorizationInput,
    ReworkClaimCloseInput,
    ReworkClaimCreate,
    ReworkClaimRead,
    ServiceOrderCreate,
    ServiceOrderUpdate,
    TaskRead,
    TransferLineRead,
    TransferRead,
    UpsellCreate,
    WarrantyClaimCreate,
    WarrantyClaimRead,
)
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.fifo import allocate_fifo
from app.modules.warehouse.models import PartLot, StockMovement
from app.modules.administracion.models import PurchaseRequest, SupplierClaim

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
            discount_label=payload.discount_label,
            notes=payload.notes,
            scheduled_at=payload.scheduled_at,
            technician_user_id=payload.technician_user_id,
            advisor_user_id=payload.advisor_user_id,
            bay_id=payload.bay_id,
            intake_mileage=payload.intake_mileage,
            customer_reason=payload.customer_reason,
            promised_at=payload.promised_at,
            sequence_number=next_seq,
        )
        self.db.add(order)
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def update_order(self, order_id: uuid.UUID, payload: ServiceOrderUpdate) -> ServiceOrder:
        if payload.status == ServiceOrderStatus.ORDEN_CERRADA:
            if payload.model_fields_set != {"status"}:
                raise BadRequestError("Cerrar la orden es una operación separada de la edición.")
            return await self.close_order(order_id)
        order = await require_editable_order(self.db, order_id)

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

        if payload.scheduled_at is not None:
            order.scheduled_at = payload.scheduled_at

        if payload.notes is not None:
            order.notes = payload.notes

        if payload.intake_mileage is not None:
            order.intake_mileage = payload.intake_mileage

        await self.db.commit()
        await self.db.refresh(order)
        return order

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
        linked_parts = [p for p in tempario.parts if p.part_id is not None]
        if linked_parts:
            transfer = await self._get_or_create_pending_transfer(service_order_id)
            for tp in linked_parts:
                part = await self.db.get(Part, tp.part_id)
                if part is not None:
                    await self._add_line_to_transfer(
                        transfer, part.id, tp.quantity, payer=payer, service_order_task_id=task.id
                    )

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
        await self._add_line_to_transfer(transfer, part.id, quantity, payer=payer)
        await self.db.commit()
        await self.db.refresh(transfer)
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
                lots = list(
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
                allocations = allocate_fifo(lots, line.quantity)
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
                    lot.quantity_remaining -= take
                    self.db.add(
                        StockMovement(
                            filial_id=order.filial_id,
                            warehouse_id=lot.warehouse_id,
                            part_id=line.part_id,
                            movement_type=MovementType.SALIDA,
                            quantity=take,
                            unit_cost=lot.unit_cost,
                            reference=transfer.code,
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
    ) -> None:
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
        # the counter-sale flow — never off a single "latest" lot. This is
        # a read-only preview: no stock is touched until dispatch.
        lots = list(
            (
                await self.db.execute(
                    select(PartLot)
                    .where(
                        PartLot.filial_id == order.filial_id,
                        PartLot.part_id == part_id,
                        PartLot.quantity_remaining > 0,
                    )
                    .order_by(PartLot.received_at, PartLot.id)
                )
            ).scalars()
        )
        allocations = allocate_fifo(lots, new_quantity)
        cost = sum((Decimal(str(lot.unit_cost)) * take for lot, take in allocations), Decimal(0))

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

        return OrderSummary(
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
        await require_editable_order(self.db, service_order_id)
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
        await require_editable_order(self.db, upsell.service_order_id)
        upsell.status = status
        if status != UpsellStatus.PENDIENTE:
            upsell.resolved_at = datetime.now(UTC)
        else:
            upsell.resolved_at = None
        await self.db.commit()
        await self.db.refresh(upsell)
        return upsell

    # Rework claims (garantía de taller) — a comeback complaint about an
    # already-invoiced order. Deliberately NOT gated by require_editable_order:
    # it records a new fact about a locked order, it doesn't modify the order.

    async def _rework_claim_to_read(self, claim: ReworkClaim, invoice: ServiceOrderInvoice) -> ReworkClaimRead:
        tempario_name = None
        if claim.tempario_id is not None:
            tempario = await self.db.get(Tempario, claim.tempario_id)
            tempario_name = tempario.name if tempario else None
        part_name = None
        if claim.part_id is not None:
            part = await self.db.get(Part, claim.part_id)
            part_name = part.name if part else None

        auto_claim_ids: list[uuid.UUID] = []
        supplier_claim_note: str | None = None
        if (
            claim.status == ReworkClaimStatus.CERRADO
            and claim.failure_category == ReworkFailureCategory.REPUESTO_DEFECTUOSO
            and claim.part_id is not None
        ):
            result = await self.db.execute(
                select(SupplierClaim.id).where(SupplierClaim.rework_claim_id == claim.id)
            )
            auto_claim_ids = [row[0] for row in result.all()]
            if not auto_claim_ids:
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
                        .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
                        .where(
                            ServiceOrderTransfer.service_order_id == claim.service_order_id,
                            ServiceOrderTransferLine.part_id == claim.part_id,
                            ServiceOrderTransfer.status == TransferStatus.PEDIDO,
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

        return ReworkClaimRead(
            id=claim.id,
            service_order_id=claim.service_order_id,
            tempario_id=claim.tempario_id,
            tempario_name=tempario_name,
            part_id=claim.part_id,
            part_name=part_name,
            failure_category=claim.failure_category,
            failure_cause=claim.failure_cause,
            claimed_at=claim.claimed_at,
            days_since_invoice=(claim.claimed_at - invoice.issued_at.date()).days,
            recorded_by_user_id=claim.recorded_by_user_id,
            note=claim.note,
            created_at=claim.created_at,
            status=claim.status,
            closed_at=claim.closed_at,
            closed_by_user_id=claim.closed_by_user_id,
            auto_generated_supplier_claim_ids=auto_claim_ids,
            supplier_claim_note=supplier_claim_note,
            authorization_status=claim.authorization_status,
            authorized_by_user_id=claim.authorized_by_user_id,
            authorized_at=claim.authorized_at,
            warranty_override=claim.warranty_override,
            warranty_override_note=claim.warranty_override_note,
            resulting_service_order_id=claim.resulting_service_order_id,
            resulting_service_order_code=resulting_service_order_code,
        )

    async def _auto_claim_defective_part(self, order: ServiceOrder, claim: ReworkClaim) -> None:
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
                    ServiceOrderTransfer.status == TransferStatus.PEDIDO,
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
                    note=f"Generado automáticamente — retrabajo {order.code}, repuesto defectuoso",
                    lot_id=lot.id,
                    purchase_request_id=purchase_request.id,
                    rework_claim_id=claim.id,
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

    async def create_rework_claim(
        self, order_id: uuid.UUID, payload: ReworkClaimCreate, recorded_by_user_id: uuid.UUID | None
    ) -> ReworkClaimRead:
        order = await self.get_order(order_id)
        invoice = await self._get_order_invoice(order_id)

        if payload.tempario_id is not None:
            task = (
                await self.db.execute(
                    select(ServiceOrderTask).where(
                        ServiceOrderTask.service_order_id == order_id,
                        ServiceOrderTask.tempario_id == payload.tempario_id,
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                raise ReworkClaimReferenceMismatchError()

        if payload.part_id is not None:
            line = (
                await self.db.execute(
                    select(ServiceOrderTransferLine)
                    .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
                    .where(
                        ServiceOrderTransfer.service_order_id == order_id,
                        ServiceOrderTransferLine.part_id == payload.part_id,
                    )
                )
            ).scalar_one_or_none()
            if line is None:
                raise ReworkClaimReferenceMismatchError()

        claim = ReworkClaim(
            filial_id=order.filial_id,
            service_order_id=order.id,
            tempario_id=payload.tempario_id,
            part_id=payload.part_id,
            failure_category=payload.failure_category,
            failure_cause=payload.failure_cause,
            claimed_at=payload.claimed_at,
            recorded_by_user_id=recorded_by_user_id,
            note=payload.note,
        )
        self.db.add(claim)
        await self.db.commit()
        await self.db.refresh(claim)
        return await self._rework_claim_to_read(claim, invoice)

    async def close_rework_claim(
        self, claim_id: uuid.UUID, payload: ReworkClaimCloseInput, closed_by_user_id: uuid.UUID | None
    ) -> ReworkClaimRead:
        claim = await self.db.get(ReworkClaim, claim_id)
        if claim is None:
            raise ReworkClaimNotFoundError(str(claim_id))
        if claim.status == ReworkClaimStatus.CERRADO:
            raise ReworkClaimAlreadyClosedError()

        if payload.failure_category is not None:
            claim.failure_category = payload.failure_category
        if claim.failure_category is None:
            raise FailureCategoryRequiredError()

        if claim.failure_category == ReworkFailureCategory.REPUESTO_DEFECTUOSO and claim.part_id is not None:
            order = await self.get_order(claim.service_order_id)
            await self._auto_claim_defective_part(order, claim)

        if payload.note:
            claim.note = f"{claim.note}\n{payload.note}" if claim.note else payload.note
        claim.status = ReworkClaimStatus.CERRADO
        claim.closed_at = datetime.now(UTC)
        claim.closed_by_user_id = closed_by_user_id

        await self.db.commit()
        await self.db.refresh(claim)
        invoice = await self._get_order_invoice(claim.service_order_id)
        return await self._rework_claim_to_read(claim, invoice)

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

    async def _open_order_from_claim(
        self, original_order: ServiceOrder, claim: ReworkClaim, order_type, payload: ReworkClaimAuthorizationInput
    ) -> ServiceOrder:
        next_seq = await self._next_sequence_number(original_order.filial_id)
        new_order = ServiceOrder(
            filial_id=original_order.filial_id,
            vehicle_id=original_order.vehicle_id,  # inherited — never editable afterward (no field on ServiceOrderUpdate)
            order_type=order_type,
            advisor_user_id=original_order.advisor_user_id,
            intake_mileage=payload.intake_mileage if payload.intake_mileage is not None else original_order.intake_mileage,
            customer_reason=f"Retrabajo de garantía — {claim.failure_cause}",
            promised_at=payload.promised_at or date.today(),
            sequence_number=next_seq,
        )
        self.db.add(new_order)
        await self.db.flush()

        # An approved claim's inherited work is on the shop's dime; a
        # rejected one bills the client normally.
        payer = ServiceOrderPayer.GARANTIA_TALLER if order_type == ServiceOrderType.RETRABAJO else ServiceOrderPayer.CLIENTE

        if claim.tempario_id is not None:
            await self.add_task(new_order.id, claim.tempario_id, payer=payer)
        if claim.part_id is not None:
            quantity = await self._line_quantity_for_part(original_order.id, claim.part_id)
            await self.add_transfer_line(new_order.id, claim.part_id, quantity, payer=payer)

        return new_order

    async def authorize_rework_claim(
        self, claim_id: uuid.UUID, payload: ReworkClaimAuthorizationInput, authorized_by_user_id: uuid.UUID | None
    ) -> ReworkClaimRead:
        claim = await self.db.get(ReworkClaim, claim_id)
        if claim is None:
            raise ReworkClaimNotFoundError(str(claim_id))
        if claim.authorization_status != ReworkAuthorizationStatus.PENDIENTE:
            raise ReworkClaimAlreadyAuthorizedError()

        original_order = await self.get_order(claim.service_order_id)

        if payload.decision == "aprobado":
            vehicle = await self.db.get(Vehicle, original_order.vehicle_id)
            has_vigente_warranty = False
            if vehicle is not None and vehicle.vin:
                from app.modules.post_ventas.exceptions import VehicleWarrantyNotFoundError
                from app.modules.post_ventas.service import PostVentasService

                try:
                    warranty = await PostVentasService(self.db).get_vehicle_warranty_by_vin(
                        original_order.filial_id, vehicle.vin
                    )
                    has_vigente_warranty = warranty.status == "vigente"
                except VehicleWarrantyNotFoundError:
                    has_vigente_warranty = False

            if not has_vigente_warranty:
                if not payload.warranty_override:
                    raise VehicleWarrantyRequiredError()
                if not payload.warranty_override_note:
                    raise WarrantyOverrideNoteRequiredError()

            new_order = await self._open_order_from_claim(
                original_order, claim, ServiceOrderType.RETRABAJO, payload
            )
            claim.warranty_override = payload.warranty_override
            claim.warranty_override_note = payload.warranty_override_note
            claim.authorization_status = ReworkAuthorizationStatus.APROBADO
        else:
            new_order = await self._open_order_from_claim(
                original_order, claim, ServiceOrderType.REGULAR, payload
            )
            claim.authorization_status = ReworkAuthorizationStatus.RECHAZADO

        claim.resulting_service_order_id = new_order.id
        claim.authorized_by_user_id = authorized_by_user_id
        claim.authorized_at = datetime.now(UTC)

        await self.db.commit()
        await self.db.refresh(claim)
        invoice = await self._get_order_invoice(claim.service_order_id)
        return await self._rework_claim_to_read(claim, invoice)

    async def list_rework_claims(self, order_id: uuid.UUID) -> list[ReworkClaimRead]:
        result = await self.db.execute(
            select(ServiceOrderInvoice).where(ServiceOrderInvoice.service_order_id == order_id)
        )
        invoice = result.scalar_one_or_none()
        if invoice is None:
            return []
        result = await self.db.execute(
            select(ReworkClaim)
            .where(ReworkClaim.service_order_id == order_id)
            .order_by(ReworkClaim.claimed_at.desc())
        )
        claims = list(result.scalars().all())
        return [await self._rework_claim_to_read(c, invoice) for c in claims]

    async def _get_vehicle_with_client(self, vehicle_id: uuid.UUID) -> Vehicle:
        from app.modules.clients.exceptions import VehicleNotFoundError

        result = await self.db.execute(
            select(Vehicle).options(selectinload(Vehicle.client)).where(Vehicle.id == vehicle_id)
        )
        vehicle = result.scalar_one_or_none()
        if vehicle is None:
            raise VehicleNotFoundError(str(vehicle_id))
        return vehicle

    async def get_vehicle_warranties_for_claim(self, vehicle_id: uuid.UUID):
        vehicle = await self._get_vehicle_with_client(vehicle_id)
        if not vehicle.vin:
            return vehicle.client.filial_id, []

        from app.modules.clients.service import ClientService
        from app.modules.post_ventas.service import PostVentasService

        current_mileage = await ClientService(self.db).get_current_mileage(vehicle_id)
        warranties = await PostVentasService(self.db).list_vigente_warranties_by_vin(
            vehicle.client.filial_id, vehicle.vin, current_mileage
        )
        return vehicle.client.filial_id, warranties

    async def create_warranty_claim(
        self, payload: WarrantyClaimCreate, recorded_by_user_id: uuid.UUID | None
    ) -> WarrantyClaimRead:
        vehicle = await self._get_vehicle_with_client(payload.vehicle_id)
        filial_id = vehicle.client.filial_id

        from app.modules.clients.service import ClientService

        current_mileage = await ClientService(self.db).get_current_mileage(payload.vehicle_id)
        mileage_inconsistent = current_mileage is not None and payload.reported_mileage < current_mileage

        matched_ids: set[uuid.UUID] = set()
        if vehicle.vin:
            result = await self.db.execute(
                select(VehicleWarranty.id).where(
                    VehicleWarranty.id.in_(payload.warranty_ids), VehicleWarranty.vin == vehicle.vin
                )
            )
            matched_ids = {row[0] for row in result.all()}
        if matched_ids != set(payload.warranty_ids):
            raise WarrantyClaimWarrantyMismatchError()

        claim = WarrantyClaim(
            filial_id=filial_id,
            vehicle_id=payload.vehicle_id,
            reported_symptom=payload.reported_symptom,
            reported_mileage=payload.reported_mileage,
            vehicle_mileage_at_claim=current_mileage,
            mileage_inconsistent=mileage_inconsistent,
            recorded_by_user_id=recorded_by_user_id,
        )
        self.db.add(claim)
        await self.db.flush()
        for warranty_id in payload.warranty_ids:
            self.db.add(WarrantyClaimWarranty(warranty_claim_id=claim.id, vehicle_warranty_id=warranty_id))
        await self.db.commit()
        await self.db.refresh(claim)
        return self._warranty_claim_to_read(claim, vehicle)

    async def list_warranty_claims(self, filial_id: uuid.UUID) -> list[WarrantyClaimRead]:
        result = await self.db.execute(
            select(WarrantyClaim)
            .options(selectinload(WarrantyClaim.warranty_links))
            .where(WarrantyClaim.filial_id == filial_id)
            .order_by(WarrantyClaim.created_at.desc())
        )
        claims = list(result.scalars().all())
        vehicle_ids = {c.vehicle_id for c in claims}
        vehicles: dict[uuid.UUID, Vehicle] = {}
        if vehicle_ids:
            result = await self.db.execute(
                select(Vehicle).options(selectinload(Vehicle.client)).where(Vehicle.id.in_(vehicle_ids))
            )
            vehicles = {v.id: v for v in result.scalars()}
        return [self._warranty_claim_to_read(c, vehicles[c.vehicle_id]) for c in claims]

    def _warranty_claim_to_read(self, claim: WarrantyClaim, vehicle: Vehicle) -> WarrantyClaimRead:
        return WarrantyClaimRead(
            id=claim.id,
            filial_id=claim.filial_id,
            vehicle_id=claim.vehicle_id,
            vehicle_plate=vehicle.plate,
            vehicle_vin=vehicle.vin,
            client_name=vehicle.client.full_name,
            reported_symptom=claim.reported_symptom,
            reported_mileage=claim.reported_mileage,
            vehicle_mileage_at_claim=claim.vehicle_mileage_at_claim,
            mileage_inconsistent=claim.mileage_inconsistent,
            status=claim.status,
            warranty_ids=[link.vehicle_warranty_id for link in claim.warranty_links],
            recorded_by_user_id=claim.recorded_by_user_id,
            created_at=claim.created_at,
        )
