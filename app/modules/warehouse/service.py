import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.warehouse.enums import MovementReason, MovementType, TransferStatus
from app.modules.warehouse.exceptions import (
    InsufficientStockError,
    InvalidTransferStatusTransitionError,
    SameWarehouseError,
    TransferNotFoundError,
    WarehouseNotFoundError,
)
from app.modules.warehouse.models import PartLot, StockMovement, Transfer, TransferLine, Warehouse
from app.modules.warehouse.schemas import (
    BulkLotItem,
    BulkLotReview,
    BulkLotReviewItem,
    InventoryRow,
    LotLineInput,
    PartLotRead,
    StockInCreate,
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

        for item in review.new:
            part = Part(
                filial_id=filial_id, code=item.part_code, name=item.part_name,
                category=item.category or "Sin categoría", brand="Sin marca",
                application="Universal", unit="Unidad", price=0,
                stock_quantity=0, min_stock=0,
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
        result = await self.db.execute(
            select(PartLot)
            .where(
                PartLot.warehouse_id == warehouse_id,
                PartLot.part_id == part_id,
                PartLot.quantity_remaining > 0,
            )
            .order_by(PartLot.received_at)
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
        self, filial_id: uuid.UUID, warehouse_id: uuid.UUID | None = None, search: str | None = None
    ) -> list[InventoryRow]:
        query = select(PartLot).where(PartLot.filial_id == filial_id, PartLot.quantity_remaining > 0)
        if warehouse_id:
            query = query.where(PartLot.warehouse_id == warehouse_id)
        result = await self.db.execute(query.order_by(PartLot.received_at))
        lots = list(result.scalars().all())

        warehouses = {w.id: w for w in await self.list_warehouses(filial_id)}

        totals: dict[tuple[uuid.UUID, uuid.UUID], int] = {}
        latest_location: dict[tuple[uuid.UUID, uuid.UUID], str | None] = {}
        part_ids: set[uuid.UUID] = set()
        for lot in lots:
            key = (lot.part_id, lot.warehouse_id)
            totals[key] = totals.get(key, 0) + lot.quantity_remaining
            if lot.location:
                latest_location[key] = lot.location
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
                    average_cost=await self.get_average_cost(part.id, warehouse.id),
                    location=latest_location.get((part_id, warehouse_id_)),
                    min_stock=part.min_stock,
                )
            )

        rows.sort(key=lambda r: (r.part_name, r.warehouse_name))
        return rows

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
        self, filial_id: uuid.UUID, part_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None
    ) -> list[PartLot]:
        query = select(PartLot).where(PartLot.filial_id == filial_id).order_by(PartLot.received_at)
        if part_id:
            query = query.where(PartLot.part_id == part_id)
        if warehouse_id:
            query = query.where(PartLot.warehouse_id == warehouse_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

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
