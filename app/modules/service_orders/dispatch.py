"""Submission reserves stock; warehouse dispatch consumes it exactly once."""

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError
from app.modules.clients.models import Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.parts.pricing import price_parts_cost
from app.modules.service_orders.enums import ServiceOrderStatus, TransferStatus
from app.modules.service_orders.exceptions import TransferNotFoundError
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
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.enums import TransferStatus as WarehouseTransferStatus
from app.modules.warehouse.fifo import allocate_fifo_preview
from app.modules.warehouse.models import PartLot, StockMovement, Transfer, TransferLine, Warehouse
from app.modules.warehouse.reservations import available_lots, reserved_by_lot


class WorkshopDispatchService:
    def __init__(self, db):
        self.db = db

    async def request(self, request_id, *, lock=False):
        statement = (
            select(Request)
            .options(selectinload(Request.lines).selectinload(Line.allocations))
            .where(Request.id == request_id)
        )
        if lock:
            # Serialize workflow and FIFO reservations within the filial.
            filial_id = (
                await self.db.execute(
                    select(ServiceOrder.filial_id)
                    .join(Request, Request.service_order_id == ServiceOrder.id)
                    .where(Request.id == request_id)
                )
            ).scalar_one_or_none()
            if filial_id is None:
                raise TransferNotFoundError(str(request_id))
            await self.db.execute(select(Filial.id).where(Filial.id == filial_id).with_for_update())
            statement = statement.with_for_update().execution_options(populate_existing=True)
        request = (await self.db.execute(statement)).scalar_one_or_none()
        if request is None:
            raise TransferNotFoundError(str(request_id))
        return request

    async def preview(self, request_id, *, lock=False):
        request = await self.request(request_id, lock=lock)
        order = await self.db.get(ServiceOrder, request.service_order_id)
        warehouses = list(
            (
                await self.db.execute(
                    select(Warehouse)
                    .where(Warehouse.filial_id == order.filial_id, Warehouse.is_active.is_(True))
                    .order_by(
                        Warehouse.is_workshop_default.desc(), Warehouse.created_at, Warehouse.id
                    )
                )
            ).scalars()
        )
        if not warehouses:
            raise BadRequestError(
                "Configura un almacén activo para atender el taller.",
                error_code="workshop_warehouse_required",
            )
        destination = next((w for w in warehouses if w.id == request.warehouse_id), warehouses[0])
        vehicle = await self.db.get(Vehicle, order.vehicle_id)
        plan = {
            "id": str(request.id),
            "order_code": order.code,
            "vehicle_label": f"{vehicle.brand} {vehicle.model} · {vehicle.plate or 'Sin placa'}",
            "warehouse_id": str(destination.id),
            "warehouse_name": destination.name,
            "lines": [],
        }
        names = {w.id: w.name for w in warehouses}
        held = defaultdict(int)
        for line in sorted(request.lines, key=lambda item: (str(item.part_id), str(item.id))):
            if lock:
                part = (
                    await self.db.execute(
                        select(Part).where(Part.id == line.part_id).with_for_update()
                    )
                ).scalar_one()
            else:
                part = await self.db.get(Part, line.part_id)
            statement = (
                select(PartLot)
                .where(
                    PartLot.filial_id == order.filial_id,
                    PartLot.part_id == line.part_id,
                    PartLot.warehouse_id.in_(list(names)),
                    PartLot.quantity_remaining > 0,
                )
                .order_by(
                    (PartLot.warehouse_id == destination.id).desc(), PartLot.received_at, PartLot.id
                )
            )
            if lock:
                statement = statement.with_for_update().execution_options(populate_existing=True)
            lots = list((await self.db.execute(statement)).scalars())
            reservations = await reserved_by_lot(
                self.db, line.part_id, exclude_transfer_id=request.id
            )
            for lot in lots:
                reservations[lot.id] = reservations.get(lot.id, 0) + held[lot.id]
            allocations, shortfall = allocate_fifo_preview(
                available_lots(lots, reservations), line.quantity
            )
            allocation_rows = []
            local = 0
            for lot, quantity in allocations:
                held[lot.id] += quantity
                if lot.warehouse_id == destination.id:
                    local += quantity
                allocation_rows.append(
                    {
                        "lot_id": str(lot.id),
                        "warehouse_id": str(lot.warehouse_id),
                        "warehouse_name": names[lot.warehouse_id],
                        "quantity": quantity,
                        "unit_cost": str(lot.unit_cost),
                    }
                )
            plan["lines"].append(
                {
                    "id": str(line.id),
                    "part_id": str(line.part_id),
                    "part_code": part.code,
                    "part_name": part.name,
                    "quantity": line.quantity,
                    "local_quantity": local,
                    "transfer_quantity": line.quantity - local - shortfall,
                    "shortfall_quantity": shortfall,
                    "allocations": allocation_rows,
                }
            )
        return request, order, plan

    async def submit(self, request_id, user_id=None):
        from uuid import UUID

        from app.modules.service_orders.guards import require_editable_order

        try:
            request = await self.request(request_id, lock=True)
            await require_editable_order(self.db, request.service_order_id)
            request, order, plan = await self.preview(request_id, lock=True)
            if request.status != TransferStatus.PENDIENTE:
                return request
            if not request.lines:
                raise BadRequestError("Agrega al menos un repuesto antes de enviar la solicitud.")
            destination_id = UUID(plan["warehouse_id"])
            by_id = {str(line.id): line for line in request.lines}
            transfers = {}
            for row in plan["lines"]:
                line = by_id[row["id"]]
                line.shortfall_quantity = row["shortfall_quantity"]
                line.allocations = [
                    Allocation(
                        lot_id=UUID(item["lot_id"]),
                        warehouse_id=UUID(item["warehouse_id"]),
                        quantity=item["quantity"],
                        unit_cost=Decimal(item["unit_cost"]),
                    )
                    for item in row["allocations"]
                ]
                cost = sum(
                    (Decimal(item["unit_cost"]) * item["quantity"] for item in row["allocations"]),
                    Decimal(0),
                )
                if not row["shortfall_quantity"]:
                    line.cost_total = cost
                    line.unit_price, line.line_total = price_parts_cost(
                        cost, line.quantity, order.discount_label
                    )
                grouped = defaultdict(list)
                for item in row["allocations"]:
                    if UUID(item["warehouse_id"]) != destination_id:
                        grouped[item["warehouse_id"]].append(item)
                for origin, items in grouped.items():
                    transfer = transfers.get(origin)
                    if transfer is None:
                        await self.db.flush()
                        sequence = (
                            await self.db.execute(
                                select(func.max(Transfer.sequence_number)).where(
                                    Transfer.filial_id == order.filial_id
                                )
                            )
                        ).scalar() or 3000
                        transfer = Transfer(
                            filial_id=order.filial_id,
                            sequence_number=sequence + 1,
                            origin_warehouse_id=UUID(origin),
                            destination_warehouse_id=destination_id,
                            workshop_request_id=request.id,
                            note=f"Traslado automático para {order.code} · {request.code}",
                        )
                        self.db.add(transfer)
                        await self.db.flush()
                        transfers[origin] = transfer
                    quantity = sum(item["quantity"] for item in items)
                    cost = sum(Decimal(item["unit_cost"]) * item["quantity"] for item in items)
                    self.db.add(
                        TransferLine(
                            transfer_id=transfer.id,
                            workshop_request_line_id=line.id,
                            part_id=line.part_id,
                            quantity=quantity,
                            unit_cost=cost / quantity,
                        )
                    )
            request.warehouse_id = destination_id
            request.status = TransferStatus.PEDIDO
            request.fulfilled_by_user_id = user_id
            request.fulfilled_at = datetime.now(UTC)
            request.stock_deducted = False
            if any(row["shortfall_quantity"] for row in plan["lines"]):
                request.backorder_notified_at = datetime.now(UTC)
            await self.db.commit()
            return request
        except Exception:
            await self.db.rollback()
            raise

    async def start(self, request_id, user_id=None):
        request = await self.request(request_id, lock=True)
        order = await self.db.get(ServiceOrder, request.service_order_id)
        if order.status == ServiceOrderStatus.CANCELADO:
            raise BadRequestError("No se puede preparar una ODS cancelada.")
        if request.status != TransferStatus.PEDIDO:
            raise BadRequestError("Solo se pueden preparar solicitudes enviadas.")
        if request.preparation_started_at is None:
            request.preparation_started_at = datetime.now(UTC)
            request.preparation_started_by_user_id = user_id
        await self.db.commit()
        return request

    async def dispatch(self, request_id, user_id=None):
        from app.modules.parts.service import _sync_availability
        from app.modules.service_orders.exceptions import InvalidTransferStatusTransitionError

        try:
            request = await self.request(request_id, lock=True)
            if request.status == TransferStatus.COMPLETADO:
                return request
            if request.status != TransferStatus.PEDIDO:
                raise InvalidTransferStatusTransitionError(
                    request.status.value, TransferStatus.COMPLETADO.value
                )
            order = (
                await self.db.execute(
                    select(ServiceOrder)
                    .where(ServiceOrder.id == request.service_order_id)
                    .with_for_update(of=ServiceOrder)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            if order.status == ServiceOrderStatus.CANCELADO:
                raise BadRequestError("No se puede despachar una ODS cancelada.")
            if not request.stock_deducted:
                linked = list(
                    (
                        await self.db.execute(
                            select(Transfer).where(Transfer.workshop_request_id == request.id)
                        )
                    ).scalars()
                )
                if any(t.status != WarehouseTransferStatus.COMPLETADA for t in linked):
                    raise BadRequestError(
                        "Recibe los traslados pendientes antes de completar el despacho.",
                        error_code="workshop_transfer_pending",
                    )
                # Refill backorders from newly received inventory, without touching
                # other reservations. Destination-only: no silent cross-warehouse pick.
                for line in sorted(
                    request.lines, key=lambda item: (str(item.part_id), str(item.id))
                ):
                    await self.db.execute(
                        select(Part).where(Part.id == line.part_id).with_for_update()
                    )
                    lots = list(
                        (
                            await self.db.execute(
                                select(PartLot)
                                .where(
                                    PartLot.part_id == line.part_id,
                                    PartLot.warehouse_id == request.warehouse_id,
                                    PartLot.quantity_remaining > 0,
                                )
                                .order_by(PartLot.received_at, PartLot.id)
                                .with_for_update()
                                .execution_options(populate_existing=True)
                            )
                        ).scalars()
                    )
                    held = await reserved_by_lot(
                        self.db, line.part_id, exclude_transfer_id=request.id
                    )
                    allocations, shortfall = allocate_fifo_preview(
                        available_lots(lots, held), line.quantity
                    )
                    if shortfall:
                        from app.modules.warehouse.exceptions import InsufficientStockError

                        part = await self.db.get(Part, line.part_id)
                        raise InsufficientStockError(
                            line.quantity - shortfall,
                            line.quantity,
                            part_id=line.part_id,
                            part_name=part.name,
                        )
                    line.shortfall_quantity = 0
                    line.allocations = [
                        Allocation(
                            lot_id=lot.id,
                            warehouse_id=lot.warehouse_id,
                            unit_cost=lot.unit_cost,
                            quantity=quantity,
                        )
                        for lot, quantity in allocations
                    ]
                    cost = sum(
                        (Decimal(str(lot.unit_cost)) * quantity for lot, quantity in allocations),
                        Decimal(0),
                    )
                    line.cost_total = cost
                    line.unit_price, line.line_total = price_parts_cost(
                        cost, line.quantity, order.discount_label
                    )
                    by_id = {lot.id: lot for lot in lots}
                    for lot, quantity in allocations:
                        by_id[lot.id].quantity_remaining -= quantity
                        self.db.add(
                            StockMovement(
                                filial_id=order.filial_id,
                                warehouse_id=lot.warehouse_id,
                                part_id=line.part_id,
                                movement_type=MovementType.SALIDA,
                                quantity=quantity,
                                unit_cost=lot.unit_cost,
                                reference=request.code,
                                responsible_user_id=user_id,
                            )
                        )
                    part = (
                        await self.db.execute(
                            select(Part)
                            .where(Part.id == line.part_id)
                            .with_for_update()
                            .execution_options(populate_existing=True)
                        )
                    ).scalar_one()
                    part.stock_quantity = max(0, part.stock_quantity - line.quantity)
                    _sync_availability(part)
                    await self.db.flush()
                request.stock_deducted = True
            request.status = TransferStatus.COMPLETADO
            request.completed_by_user_id = user_id
            request.completed_at = datetime.now(UTC)
            await self.db.commit()
            return request
        except Exception:
            await self.db.rollback()
            raise
