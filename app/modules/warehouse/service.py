import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.warehouse.enums import MovementReason, MovementType, TransferStatus
from app.modules.warehouse.exceptions import (
    InsufficientStockError,
    InvalidTransferStatusTransitionError,
    NoStockAtWarehouseError,
    PartLotNotFoundError,
    SameWarehouseError,
    StockInReasonNameAlreadyExistsError,
    StockInReasonNotFoundError,
    TransferNotFoundError,
    WarehouseNotFoundError,
)
from app.modules.warehouse.models import (
    PartLot,
    StockInReason,
    StockMovement,
    Transfer,
    TransferLine,
    Warehouse,
)
from app.modules.warehouse.schemas import (
    BulkLotItem,
    BulkLotReview,
    BulkLotReviewItem,
    InventoryRow,
    LotLineInput,
    LotOutboundMovementRead,
    PartLotDetailRead,
    PartLotRead,
    PartSaleRequestLineRead,
    PartSaleRequestRead,
    ServiceOrderPartRequestLineRead,
    ServiceOrderPartRequestLineWarehouse,
    ServiceOrderPartRequestRead,
    StockInCreate,
    StockInReasonCreate,
    StockInReasonUpdate,
    StockOutCreate,
    TransferCreate,
    TransferLineRead,
    TransferRead,
)
from app.modules.parts.models import Part
from app.modules.parts.service import _sync_availability

REASON_TO_MOVEMENT_TYPE: dict[MovementReason, MovementType] = {
    MovementReason.CONSUMO_ODS: MovementType.SALIDA,
    MovementReason.AJUSTE_INVENTARIO: MovementType.SALIDA,
    MovementReason.OTRO: MovementType.SALIDA,
    MovementReason.DEVOLUCION_PROVEEDOR: MovementType.DEVOLUCION,
}

# Preloaded for every holding — matches the two hardcoded defaults this
# catalog replaces (the frontend's old localStorage list, and StockInCreate's
# schema default). New ones are seeded at holding creation; existing ones are
# backfilled by the migration that introduced this catalog.
DEFAULT_STOCK_IN_REASONS = [
    "Compra directa",
    "Ajuste de inventario",
    "Devolución de cliente",
    "Otro",
]

TRANSFER_TRANSITIONS: dict[TransferStatus, set[TransferStatus]] = {
    TransferStatus.PEDIDO: {TransferStatus.EN_PROCESO, TransferStatus.CANCELADA},
    TransferStatus.EN_PROCESO: {TransferStatus.COMPLETADA, TransferStatus.CANCELADA},
    TransferStatus.COMPLETADA: set(),
    TransferStatus.CANCELADA: set(),
}


def _lot_to_read(lot: PartLot) -> PartLotRead:
    return PartLotRead(
        id=lot.id,
        code=lot.code,
        warehouse_id=lot.warehouse_id,
        part_id=lot.part_id,
        quantity_received=lot.quantity_received,
        quantity_remaining=lot.quantity_remaining,
        unit_cost=float(lot.unit_cost),
        location=lot.location,
        note=lot.note,
        received_at=lot.received_at,
    )


class AlmacenService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # Warehouses

    async def list_warehouses(self, filial_id: uuid.UUID) -> list[Warehouse]:
        result = await self.db.execute(
            select(Warehouse).where(Warehouse.filial_id == filial_id).order_by(Warehouse.created_at)
        )
        return list(result.scalars().all())

    async def get_warehouse(self, warehouse_id: uuid.UUID) -> Warehouse:
        warehouse = await self.db.get(Warehouse, warehouse_id)
        if warehouse is None:
            raise WarehouseNotFoundError(str(warehouse_id))
        return warehouse

    async def create_warehouse(self, filial_id: uuid.UUID, name: str) -> Warehouse:
        warehouse = Warehouse(filial_id=filial_id, name=name)
        self.db.add(warehouse)
        await self.db.commit()
        await self.db.refresh(warehouse)
        return warehouse

    async def update_warehouse(
        self, warehouse_id: uuid.UUID, name: str | None, is_active: bool | None
    ) -> Warehouse:
        warehouse = await self.get_warehouse(warehouse_id)
        if name is not None:
            warehouse.name = name
        if is_active is not None:
            warehouse.is_active = is_active
        await self.db.commit()
        await self.db.refresh(warehouse)
        return warehouse

    # Stock-in reasons (Ajustes → Motivos de Entrada) — holding-wide.

    async def _holding_id_for_filial(self, filial_id: uuid.UUID) -> uuid.UUID:
        from app.core.exceptions import BadRequestError
        from app.modules.filiales.models import Filial

        filial = await self.db.get(Filial, filial_id)
        if filial is None:
            raise BadRequestError("La filial no existe.")
        return filial.holding_id

    async def list_stock_in_reasons(
        self, holding_id: uuid.UUID, include_inactive: bool = False
    ) -> list[StockInReason]:
        query = (
            select(StockInReason)
            .where(StockInReason.holding_id == holding_id)
            .order_by(StockInReason.name)
        )
        if not include_inactive:
            query = query.where(StockInReason.is_active.is_(True))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _get_stock_in_reason(self, reason_id: uuid.UUID, holding_id: uuid.UUID) -> StockInReason:
        result = await self.db.execute(
            select(StockInReason).where(
                StockInReason.id == reason_id, StockInReason.holding_id == holding_id
            )
        )
        reason = result.scalar_one_or_none()
        if reason is None:
            raise StockInReasonNotFoundError(str(reason_id))
        return reason

    async def create_stock_in_reason(
        self, holding_id: uuid.UUID, payload: StockInReasonCreate
    ) -> StockInReason:
        await self._ensure_stock_in_reason_name_is_available(holding_id, payload.name)
        reason = StockInReason(holding_id=holding_id, name=payload.name.strip())
        self.db.add(reason)
        await self.db.commit()
        await self.db.refresh(reason)
        return reason

    async def update_stock_in_reason(
        self, reason_id: uuid.UUID, holding_id: uuid.UUID, payload: StockInReasonUpdate
    ) -> StockInReason:
        reason = await self._get_stock_in_reason(reason_id, holding_id)
        if payload.name and payload.name.strip() != reason.name:
            await self._ensure_stock_in_reason_name_is_available(holding_id, payload.name)
            reason.name = payload.name.strip()
        await self.db.commit()
        await self.db.refresh(reason)
        return reason

    async def set_stock_in_reason_active(
        self, reason_id: uuid.UUID, holding_id: uuid.UUID, is_active: bool
    ) -> StockInReason:
        reason = await self._get_stock_in_reason(reason_id, holding_id)
        reason.is_active = is_active
        await self.db.commit()
        await self.db.refresh(reason)
        return reason

    async def _ensure_stock_in_reason_name_is_available(self, holding_id: uuid.UUID, name: str) -> None:
        result = await self.db.execute(
            select(StockInReason).where(
                StockInReason.holding_id == holding_id,
                func.lower(StockInReason.name) == name.strip().lower(),
            )
        )
        if result.scalar_one_or_none() is not None:
            raise StockInReasonNameAlreadyExistsError(name)

    async def seed_default_stock_in_reasons(self, holding_id: uuid.UUID) -> None:
        for name in DEFAULT_STOCK_IN_REASONS:
            self.db.add(StockInReason(holding_id=holding_id, name=name))
        await self.db.commit()

    # Entradas (receiving stock -> creates a FIFO lot)

    async def _next_lot_number(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(PartLot.lot_number)).where(PartLot.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 100) + 1

    async def _create_single_lot(
        self,
        filial_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        line: LotLineInput,
        note: str | None,
        responsible_user_id: uuid.UUID | None = None,
    ) -> PartLot | None:
        part = await self.db.get(Part, line.part_id)
        if part is None:
            return None

        lot_number = await self._next_lot_number(filial_id)
        lot = PartLot(
            filial_id=filial_id,
            lot_number=lot_number,
            warehouse_id=warehouse_id,
            part_id=line.part_id,
            quantity_received=line.quantity,
            quantity_remaining=line.quantity,
            unit_cost=line.unit_cost,
            location=line.location,
            note=note,
            purchase_request_id=line.purchase_request_id,
        )
        self.db.add(lot)

        part.stock_quantity += line.quantity
        _sync_availability(part)

        self.db.add(
            StockMovement(
                filial_id=filial_id,
                warehouse_id=warehouse_id,
                part_id=line.part_id,
                movement_type=MovementType.ENTRADA,
                quantity=line.quantity,
                unit_cost=line.unit_cost,
                note=note,
                responsible_user_id=responsible_user_id,
            )
        )
        return lot

    async def create_stock_in(
        self, payload: StockInCreate, responsible_user_id: uuid.UUID | None = None
    ) -> list[PartLot]:
        lots: list[PartLot] = []
        for line in payload.lines:
            lot = await self._create_single_lot(
                payload.filial_id, payload.warehouse_id, line, payload.reason, responsible_user_id
            )
            if lot is not None:
                lots.append(lot)
        await self.db.commit()
        for lot in lots:
            await self.db.refresh(lot)
        return lots

    async def bulk_create_lots(
        self,
        filial_id: uuid.UUID,
        warehouse_id: uuid.UUID,
        items: list[BulkLotItem],
        responsible_user_id: uuid.UUID | None = None,
    ) -> tuple[list[PartLot], list[str]]:
        review = await self.review_bulk_lots(filial_id, items)
        if review.conflicts:
            raise ValueError("Bulk import contains catalog name conflicts")

        result = await self.db.execute(select(Part).where(Part.filial_id == filial_id))
        parts_by_code = {p.code.strip().casefold(): p for p in result.scalars().all()}

        if review.new:
            from app.modules.filiales.models import Filial
            from app.modules.parts.service import PartsService

            filial = await self.db.get(Filial, filial_id)
            parts_service = PartsService(self.db)
            for item in review.new:
                category = await parts_service.get_or_create_category(
                    filial.holding_id, item.category or "Sin categoría"
                )
                part = Part(
                    filial_id=filial_id, code=item.part_code, name=item.part_name,
                    category_id=category.id, unit="Unidad", price=0, stock_quantity=0,
                )
                _sync_availability(part)
                self.db.add(part)
                await self.db.flush()
                parts_by_code[part.code.strip().casefold()] = part

        created: list[PartLot] = []
        skipped: list[str] = []
        for item in items:
            part = parts_by_code.get(item.part_code.strip().casefold())
            if part is None:
                skipped.append(item.part_code)
                continue
            line = LotLineInput(
                part_id=part.id, quantity=item.quantity, unit_cost=item.unit_cost, location=item.location
            )
            lot = await self._create_single_lot(
                filial_id, warehouse_id, line, "Carga masiva", responsible_user_id
            )
            if lot is not None:
                created.append(lot)

        await self.db.commit()
        for lot in created:
            await self.db.refresh(lot)
        return created, skipped

    async def review_bulk_lots(
        self, filial_id: uuid.UUID, items: list[BulkLotItem]
    ) -> BulkLotReview:
        result = await self.db.execute(select(Part).where(Part.filial_id == filial_id))
        parts_by_code = {part.code.strip().casefold(): part for part in result.scalars().all()}
        existing: list[BulkLotReviewItem] = []
        new: list[BulkLotReviewItem] = []
        conflicts: list[BulkLotReviewItem] = []
        seen_codes: set[str] = set()
        for item in items:
            normalized_code = item.part_code.strip().casefold()
            part = parts_by_code.get(normalized_code)
            reviewed = BulkLotReviewItem(
                **item.model_dump(), catalog_name=part.name if part else None
            )
            if normalized_code in seen_codes:
                conflicts.append(reviewed.model_copy(update={"catalog_name": "Código repetido en archivo"}))
            elif part is None:
                new.append(reviewed)
            elif part.name.strip().casefold() != item.part_name.strip().casefold():
                conflicts.append(reviewed)
            else:
                existing.append(reviewed)
            seen_codes.add(normalized_code)
        return BulkLotReview(existing=existing, new=new, conflicts=conflicts)

    # Salidas (manual stock-out, e.g. consumption or returns to a supplier)

    async def create_stock_out(self, payload: StockOutCreate, responsible_user_id: uuid.UUID | None = None) -> None:
        consumed_cost = await self._consume_fifo(payload.warehouse_id, payload.part_id, payload.quantity)

        part = await self.db.get(Part, payload.part_id)
        if part is not None:
            part.stock_quantity = max(0, part.stock_quantity - payload.quantity)
            _sync_availability(part)

        self.db.add(
            StockMovement(
                filial_id=payload.filial_id,
                warehouse_id=payload.warehouse_id,
                part_id=payload.part_id,
                movement_type=REASON_TO_MOVEMENT_TYPE[payload.reason],
                quantity=payload.quantity,
                unit_cost=consumed_cost,
                reference=payload.reference,
                responsible_user_id=responsible_user_id,
            )
        )
        await self.db.commit()

    async def _consume_fifo(self, warehouse_id: uuid.UUID, part_id: uuid.UUID, quantity: int) -> float:
        """Consumes `quantity` units from the oldest lots first. Returns the
        weighted average unit cost of what was consumed. Raises if there isn't
        enough stock at that warehouse for that part."""
        # Preserve prior consumption in this transaction before refreshing locked lots.
        await self.db.flush()
        result = await self.db.execute(
            select(PartLot)
            .where(
                PartLot.warehouse_id == warehouse_id,
                PartLot.part_id == part_id,
                PartLot.quantity_remaining > 0,
            )
            .order_by(PartLot.received_at, PartLot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        lots = list(result.scalars().all())
        available = sum(lot.quantity_remaining for lot in lots)
        if available < quantity:
            raise InsufficientStockError(available, quantity)

        remaining = quantity
        consumed_cost_total = 0.0
        for lot in lots:
            if remaining <= 0:
                break
            take = min(lot.quantity_remaining, remaining)
            lot.quantity_remaining -= take
            consumed_cost_total += take * float(lot.unit_cost)
            remaining -= take

        return consumed_cost_total / quantity if quantity else 0.0

    # Transfers (stateful ODT between warehouses)

    async def _next_transfer_sequence(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(Transfer.sequence_number)).where(Transfer.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 3000) + 1

    async def create_transfer(self, payload: TransferCreate) -> Transfer:
        if payload.origin_warehouse_id == payload.destination_warehouse_id:
            raise SameWarehouseError()

        sequence_number = await self._next_transfer_sequence(payload.filial_id)
        transfer = Transfer(
            filial_id=payload.filial_id,
            sequence_number=sequence_number,
            origin_warehouse_id=payload.origin_warehouse_id,
            destination_warehouse_id=payload.destination_warehouse_id,
            note=payload.note,
        )
        self.db.add(transfer)
        await self.db.flush()

        for line in payload.lines:
            cost = await self.get_average_cost(line.part_id, payload.origin_warehouse_id)
            self.db.add(
                TransferLine(
                    transfer_id=transfer.id,
                    part_id=line.part_id,
                    quantity=line.quantity,
                    unit_cost=cost or 0.0,
                )
            )

        await self.db.commit()
        return await self._get_transfer_model(transfer.id)

    async def _get_transfer_model(self, transfer_id: uuid.UUID) -> Transfer:
        result = await self.db.execute(
            select(Transfer).options(selectinload(Transfer.lines)).where(Transfer.id == transfer_id)
        )
        transfer = result.scalar_one_or_none()
        if transfer is None:
            raise TransferNotFoundError(str(transfer_id))
        return transfer

    async def list_transfers(self, filial_id: uuid.UUID) -> list[Transfer]:
        result = await self.db.execute(
            select(Transfer)
            .options(selectinload(Transfer.lines))
            .where(Transfer.filial_id == filial_id)
            .order_by(Transfer.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_transfer(self, transfer_id: uuid.UUID) -> Transfer:
        return await self._get_transfer_model(transfer_id)

    async def update_transfer_status(
        self, transfer_id: uuid.UUID, new_status: TransferStatus, responsible_user_id: uuid.UUID | None = None
    ) -> Transfer:
        transfer = await self._get_transfer_model(transfer_id)
        if new_status == transfer.status:
            return transfer
        if new_status not in TRANSFER_TRANSITIONS.get(transfer.status, set()):
            raise InvalidTransferStatusTransitionError(transfer.status.value, new_status.value)

        if new_status == TransferStatus.COMPLETADA:
            for line in transfer.lines:
                weighted_cost = await self._consume_fifo(
                    transfer.origin_warehouse_id, line.part_id, line.quantity
                )
                lot_number = await self._next_lot_number(transfer.filial_id)
                self.db.add(
                    PartLot(
                        filial_id=transfer.filial_id,
                        lot_number=lot_number,
                        warehouse_id=transfer.destination_warehouse_id,
                        part_id=line.part_id,
                        quantity_received=line.quantity,
                        quantity_remaining=line.quantity,
                        unit_cost=weighted_cost,
                        note=f"Transferencia {transfer.code}",
                    )
                )
                self.db.add(
                    StockMovement(
                        filial_id=transfer.filial_id,
                        warehouse_id=transfer.origin_warehouse_id,
                        part_id=line.part_id,
                        movement_type=MovementType.TRANSFERENCIA_SALIDA,
                        quantity=line.quantity,
                        unit_cost=weighted_cost,
                        reference=transfer.code,
                        responsible_user_id=responsible_user_id,
                    )
                )
                self.db.add(
                    StockMovement(
                        filial_id=transfer.filial_id,
                        warehouse_id=transfer.destination_warehouse_id,
                        part_id=line.part_id,
                        movement_type=MovementType.TRANSFERENCIA_ENTRADA,
                        quantity=line.quantity,
                        unit_cost=weighted_cost,
                        reference=transfer.code,
                        responsible_user_id=responsible_user_id,
                    )
                )
            transfer.completed_at = datetime.now(UTC)
            transfer.completed_by_user_id = responsible_user_id

        transfer.status = new_status
        await self.db.commit()
        return await self._get_transfer_model(transfer_id)

    # Inventory, lots & movements

    async def get_inventory(
        self,
        filial_id: uuid.UUID,
        warehouse_id: uuid.UUID | None = None,
        search: str | None = None,
        part_id: uuid.UUID | None = None,
    ) -> list[InventoryRow]:
        query = select(PartLot).where(PartLot.filial_id == filial_id, PartLot.quantity_remaining > 0)
        if warehouse_id:
            query = query.where(PartLot.warehouse_id == warehouse_id)
        if part_id:
            query = query.where(PartLot.part_id == part_id)
        result = await self.db.execute(query.order_by(PartLot.received_at))
        lots = list(result.scalars().all())

        warehouses = {w.id: w for w in await self.list_warehouses(filial_id)}

        totals: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
        latest_location: dict[tuple[uuid.UUID, uuid.UUID], str | None] = {}
        # `lots` is already ordered oldest-first (received_at asc), so the
        # first lot seen per key is the active FIFO lot the dashboard's
        # subtitle promises — not a blended average across all remaining lots.
        fifo_front_cost: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
        part_ids: set[uuid.UUID] = set()
        for lot in lots:
            key = (lot.part_id, lot.warehouse_id)
            totals[key] = totals.get(key, 0) + lot.quantity_remaining
            if lot.location:
                latest_location[key] = lot.location
            fifo_front_cost.setdefault(key, float(lot.unit_cost))
            part_ids.add(lot.part_id)

        parts_by_id: dict[uuid.UUID, Part] = {}
        for part_id in part_ids:
            part = await self.db.get(Part, part_id)
            if part is not None:
                parts_by_id[part_id] = part

        rows: list[InventoryRow] = []
        for (part_id, warehouse_id_), quantity in totals.items():
            part = parts_by_id.get(part_id)
            warehouse = warehouses.get(warehouse_id_)
            if part is None or warehouse is None:
                continue
            if search:
                term = search.lower()
                if term not in part.code.lower() and term not in part.name.lower():
                    continue
            rows.append(
                InventoryRow(
                    part_id=part.id,
                    part_code=part.code,
                    part_name=part.name,
                    warehouse_id=warehouse.id,
                    warehouse_name=warehouse.name,
                    quantity=quantity,
                    fifo_unit_cost=fifo_front_cost.get((part_id, warehouse_id_)),
                    location=latest_location.get((part_id, warehouse_id_)),
                    min_stock=part.min_stock,
                )
            )

        rows.sort(key=lambda r: (r.part_name, r.warehouse_name))
        return rows

    async def set_inventory_location(
        self, filial_id: uuid.UUID, part_id: uuid.UUID, warehouse_id: uuid.UUID, location: str | None
    ) -> InventoryRow:
        """Edits where a part sits in a warehouse — e.g. "Estante A3". Since
        location isn't its own field (get_inventory derives it from the most
        recently received lot that has one, see the docstring there), the
        edit writes to every lot of this part still in stock at this
        warehouse, not just the newest — otherwise the edit would silently
        "revert" the moment an older lot with stale stock became the one
        left with quantity_remaining once the newest lot sold out. A future
        stock-in that itself sets a location still wins, which is correct:
        that's a real relocation, not an unrelated write clobbering this one."""
        result = await self.db.execute(
            select(PartLot).where(
                PartLot.filial_id == filial_id,
                PartLot.part_id == part_id,
                PartLot.warehouse_id == warehouse_id,
                PartLot.quantity_remaining > 0,
            )
        )
        lots = list(result.scalars().all())
        if not lots:
            raise NoStockAtWarehouseError()

        clean_location = location.strip() if location and location.strip() else None
        for lot in lots:
            lot.location = clean_location
        await self.db.commit()

        rows = await self.get_inventory(filial_id, warehouse_id, part_id=part_id)
        return rows[0]

    async def get_average_cost(
        self, part_id: uuid.UUID, warehouse_id: uuid.UUID | None = None
    ) -> float | None:
        query = select(PartLot).where(PartLot.part_id == part_id, PartLot.quantity_remaining > 0)
        if warehouse_id:
            query = query.where(PartLot.warehouse_id == warehouse_id)
        result = await self.db.execute(query)
        lots = list(result.scalars().all())
        total_qty = sum(lot.quantity_remaining for lot in lots)
        if total_qty == 0:
            return None
        total_cost = sum(lot.quantity_remaining * float(lot.unit_cost) for lot in lots)
        return total_cost / total_qty

    async def list_lots(
        self,
        filial_id: uuid.UUID,
        part_id: uuid.UUID | None = None,
        warehouse_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> list[PartLot]:
        query = select(PartLot).where(PartLot.filial_id == filial_id).order_by(PartLot.received_at)
        if part_id:
            query = query.where(PartLot.part_id == part_id)
        if warehouse_id:
            query = query.where(PartLot.warehouse_id == warehouse_id)
        if search:
            # PartLot.code is a computed "L-{lot_number}" property, not a real
            # column — match the numeric part regardless of how it's typed
            # ("L-104", "l104", "104").
            digits = search.strip().upper().removeprefix("L-").removeprefix("L")
            if digits.isdigit():
                query = query.where(PartLot.lot_number == int(digits))
            else:
                return []
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_lot(self, lot_id: uuid.UUID) -> PartLot:
        lot = await self.db.get(PartLot, lot_id)
        if lot is None:
            raise PartLotNotFoundError(str(lot_id))
        return lot

    async def get_lot_detail(self, lot: PartLot) -> PartLotDetailRead:
        """Every dispatched consumption of this exact lot — a counter sale
        line (Venta de Repuestos) or a workshop ODT line — unified into one
        chronological list. Warehouse-to-warehouse transfers aren't included:
        they don't record which origin lot they drained (a real gap in that
        flow, not something this view can paper over)."""
        part = await self.db.get(Part, lot.part_id)
        warehouse = await self.get_warehouse(lot.warehouse_id)

        movements: list[LotOutboundMovementRead] = []

        from app.modules.parts.models import PartSale, PartSaleLine, PartSaleLotAllocation

        sale_rows = await self.db.execute(
            select(PartSaleLotAllocation, PartSale)
            .join(PartSaleLine, PartSaleLine.id == PartSaleLotAllocation.part_sale_line_id)
            .join(PartSale, PartSale.id == PartSaleLine.part_sale_id)
            .where(PartSaleLotAllocation.lot_id == lot.id)
        )
        for allocation, sale in sale_rows.all():
            movements.append(
                LotOutboundMovementRead(
                    id=allocation.id,
                    source="venta_repuestos",
                    quantity=allocation.quantity,
                    unit_cost=float(allocation.unit_cost),
                    occurred_at=sale.created_at,
                    reference_code=sale.code,
                    description=f"Venta de mostrador a {sale.client_name}",
                    link_id=sale.id,
                )
            )

        from app.modules.service_orders.enums import TransferStatus as ServiceOrderTransferStatus
        from app.modules.service_orders.models import (
            ServiceOrder,
            ServiceOrderTransfer,
            ServiceOrderTransferLine,
            ServiceOrderTransferLotAllocation,
        )

        odt_rows = await self.db.execute(
            select(ServiceOrderTransferLotAllocation, ServiceOrderTransfer, ServiceOrder)
            .join(
                ServiceOrderTransferLine,
                ServiceOrderTransferLine.id == ServiceOrderTransferLotAllocation.transfer_line_id,
            )
            .join(ServiceOrderTransfer, ServiceOrderTransfer.id == ServiceOrderTransferLine.transfer_id)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderTransfer.service_order_id)
            .where(
                ServiceOrderTransferLotAllocation.lot_id == lot.id,
                # Only real, dispatched consumption — a line just added but
                # not yet "pedida a almacén" only carries a preview
                # allocation. PEDIDO or COMPLETADO both mean it shipped;
                # COMPLETADO just confirms the same dispatch was handed over.
                ServiceOrderTransfer.status.in_(
                    [ServiceOrderTransferStatus.PEDIDO, ServiceOrderTransferStatus.COMPLETADO]
                ),
            )
        )
        for allocation, transfer, order in odt_rows.all():
            movements.append(
                LotOutboundMovementRead(
                    id=allocation.id,
                    source="odt_taller",
                    quantity=allocation.quantity,
                    unit_cost=float(allocation.unit_cost),
                    occurred_at=transfer.fulfilled_at or transfer.created_at,
                    reference_code=f"{order.code} · {transfer.code}",
                    description=f"Repuesto solicitado en la orden {order.code}",
                    link_id=order.id,
                )
            )

        movements.sort(key=lambda m: m.occurred_at, reverse=True)

        return PartLotDetailRead(
            id=lot.id,
            code=lot.code,
            warehouse_id=lot.warehouse_id,
            part_id=lot.part_id,
            part_code=part.code if part else "",
            part_name=part.name if part else "",
            warehouse_name=warehouse.name,
            quantity_received=lot.quantity_received,
            quantity_remaining=lot.quantity_remaining,
            unit_cost=float(lot.unit_cost),
            location=lot.location,
            note=lot.note,
            received_at=lot.received_at,
            outbound_movements=movements,
        )

    async def list_service_order_requests(self, filial_id: uuid.UUID) -> list[ServiceOrderPartRequestRead]:
        """Dispatched ODTs ('Marcar como Pedido' from a service order) for
        this filial — how a parts request from an ODS reaches almacén staff
        in the 'Órdenes de Transferencia' screen."""
        from app.modules.clients.models import Vehicle
        from app.modules.service_orders.enums import TransferStatus as ServiceOrderTransferStatus
        from app.modules.service_orders.models import ServiceOrder, ServiceOrderTransfer

        rows = await self.db.execute(
            select(ServiceOrderTransfer, ServiceOrder, Vehicle)
            .join(ServiceOrder, ServiceOrder.id == ServiceOrderTransfer.service_order_id)
            .join(Vehicle, Vehicle.id == ServiceOrder.vehicle_id)
            .where(
                ServiceOrder.filial_id == filial_id,
                ServiceOrderTransfer.status.in_(
                    [ServiceOrderTransferStatus.PEDIDO, ServiceOrderTransferStatus.COMPLETADO]
                ),
            )
            .order_by(ServiceOrderTransfer.fulfilled_at.desc())
        )

        warehouse_names = {
            w.id: w.name
            for w in (await self.db.execute(select(Warehouse).where(Warehouse.filial_id == filial_id))).scalars()
        }

        results: list[ServiceOrderPartRequestRead] = []
        for transfer, order, vehicle in rows.all():
            lines: list[ServiceOrderPartRequestLineRead] = []
            for line in transfer.lines:
                part = await self.db.get(Part, line.part_id)
                # A line isn't scoped to one warehouse — dispatch draws FIFO
                # across every warehouse in the filial, so its quantity can
                # (rarely) split across more than one. Group its real,
                # already-dispatched allocations by warehouse so almacén
                # staff can see exactly where each unit is coming from.
                quantity_by_warehouse: dict[uuid.UUID, int] = {}
                for allocation in line.allocations:
                    quantity_by_warehouse[allocation.warehouse_id] = (
                        quantity_by_warehouse.get(allocation.warehouse_id, 0) + allocation.quantity
                    )
                lines.append(
                    ServiceOrderPartRequestLineRead(
                        part_id=line.part_id,
                        part_code=part.code if part else "",
                        part_name=part.name if part else "",
                        quantity=line.quantity,
                        warehouses=[
                            ServiceOrderPartRequestLineWarehouse(
                                warehouse_id=warehouse_id,
                                warehouse_name=warehouse_names.get(warehouse_id, "Almacén desconocido"),
                                quantity=quantity,
                            )
                            for warehouse_id, quantity in quantity_by_warehouse.items()
                        ],
                    )
                )
            results.append(
                ServiceOrderPartRequestRead(
                    id=transfer.id,
                    code=transfer.code,
                    service_order_id=order.id,
                    service_order_code=order.code,
                    vehicle_label=f"{vehicle.brand} {vehicle.model} · {vehicle.plate or 'Sin placa'}",
                    status=transfer.status.value,
                    fulfilled_at=transfer.fulfilled_at,
                    completed_at=transfer.completed_at,
                    warehouse_seen=transfer.warehouse_seen,
                    lines=lines,
                )
            )
        return results

    async def list_part_sale_requests(self, filial_id: uuid.UUID) -> list[PartSaleRequestRead]:
        """Counter parts sales (Venta de Repuestos) for this filial, surfaced
        alongside Órdenes de Servicio requests — same idea, different
        destination: a técnico waiting at a bay vs. a customer at the sales
        counter. A sale's stock is pulled FIFO the moment it's created (not
        at a separate dispatch step like an ODT), so every non-cancelled
        sale already reflects real consumption worth showing here."""
        from app.modules.parts.enums import PartSaleStatus
        from app.modules.parts.models import PartSale

        result = await self.db.execute(
            select(PartSale)
            .options(selectinload(PartSale.lines))
            .where(PartSale.filial_id == filial_id, PartSale.status != PartSaleStatus.CANCELADO)
            .order_by(PartSale.created_at.desc())
        )

        requests: list[PartSaleRequestRead] = []
        for sale in result.scalars():
            lines: list[PartSaleRequestLineRead] = []
            for line in sale.lines:
                part = await self.db.get(Part, line.part_id)
                lines.append(
                    PartSaleRequestLineRead(
                        part_id=line.part_id,
                        part_code=part.code if part else "",
                        part_name=part.name if part else "",
                        quantity=line.quantity,
                        warehouse_id=line.warehouse_id,
                        warehouse_name=line.warehouse.name if line.warehouse else None,
                    )
                )
            requests.append(
                PartSaleRequestRead(
                    id=sale.id,
                    code=sale.code,
                    client_name=sale.client_name,
                    status=sale.status.value,
                    created_at=sale.created_at,
                    lines=lines,
                )
            )
        return requests

    async def acknowledge_service_order_request(self, transfer_id: uuid.UUID) -> None:
        from app.modules.service_orders.exceptions import TransferNotFoundError as ServiceOrderTransferNotFoundError
        from app.modules.service_orders.models import ServiceOrderTransfer

        transfer = await self.db.get(ServiceOrderTransfer, transfer_id)
        if transfer is None:
            raise ServiceOrderTransferNotFoundError(str(transfer_id))
        transfer.warehouse_seen = True
        await self.db.commit()

    async def complete_service_order_request(
        self, transfer_id: uuid.UUID, completed_by_user_id: uuid.UUID
    ) -> None:
        """Almacén confirms the parts were physically handed over — pauses
        the elapsed-time counter running since the ODT was marked 'Pedido'.
        Delegates to ServiceOrderService, which owns the ServiceOrderTransfer
        model and its status transitions."""
        from app.modules.service_orders.service import ServiceOrderService

        await ServiceOrderService(self.db).complete_transfer(transfer_id, completed_by_user_id)

    async def list_movements(
        self, filial_id: uuid.UUID, part_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None
    ) -> list[StockMovement]:
        query = (
            select(StockMovement)
            .where(StockMovement.filial_id == filial_id)
            .order_by(StockMovement.created_at.desc())
        )
        if part_id:
            query = query.where(StockMovement.part_id == part_id)
        if warehouse_id:
            query = query.where(StockMovement.warehouse_id == warehouse_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())


def transfer_to_read(transfer: Transfer) -> TransferRead:
    return TransferRead(
        id=transfer.id,
        code=transfer.code,
        origin_warehouse_id=transfer.origin_warehouse_id,
        destination_warehouse_id=transfer.destination_warehouse_id,
        status=transfer.status,
        note=transfer.note,
        lines=[
            TransferLineRead(
                id=line.id,
                part_id=line.part_id,
                quantity=line.quantity,
                unit_cost=float(line.unit_cost),
                subtotal=line.quantity * float(line.unit_cost),
            )
            for line in transfer.lines
        ],
        total_cost=sum(line.quantity * float(line.unit_cost) for line in transfer.lines),
        created_at=transfer.created_at,
        completed_at=transfer.completed_at,
        completed_by_user_id=transfer.completed_by_user_id,
    )
