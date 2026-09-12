import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError
from app.modules.parts.enums import PartSaleStatus, ReturnCondition
from app.modules.parts.exceptions import (
    DispatchQuantityMismatchError,
    DispatchQuantityRequiredError,
    InvalidReturnDestinationError,
    InvalidSaleStatusTransitionError,
    MissingReturnPhotoError,
    PartCodeAlreadyExistsError,
    PartNotFoundError,
    PartSaleNotFoundError,
)
from app.modules.parts.models import (
    Part,
    PartReturn,
    PartSale,
    PartSaleLine,
    PartSaleLotAllocation,
    PartWarranty,
)
from app.modules.parts.pricing import PARTS_MULTIPLIERS
from app.modules.parts.schemas import (
    PartBulkItem,
    PartCreate,
    PartRead,
    PartReturnCreate,
    PartSaleCreate,
    PartSaleLineDispatch,
    PartUpdate,
)
from app.modules.post_ventas.models import LaborSettings
from app.modules.warehouse.enums import MovementType
from app.modules.warehouse.fifo import allocate_fifo, price_allocations
from app.modules.warehouse.models import PartLot, StockMovement, Warehouse

DEFAULT_PART_WARRANTY_DAYS = 90

SALE_TRANSITIONS: dict[PartSaleStatus, set[PartSaleStatus]] = {
    PartSaleStatus.PENDIENTE: {PartSaleStatus.PEDIDO, PartSaleStatus.CANCELADO},
    PartSaleStatus.PEDIDO: {PartSaleStatus.COMPLETADO, PartSaleStatus.CANCELADO},
    PartSaleStatus.COMPLETADO: set(),
    PartSaleStatus.CANCELADO: set(),
}


def _sync_availability(part: Part) -> None:
    """Availability is always derived from stock — never set by hand, so it
    can't drift out of sync with the actual quantity on the shelf."""
    from app.modules.parts.enums import PartAvailability

    part.availability = (
        PartAvailability.AGOTADO if part.stock_quantity <= 0 else PartAvailability.DISPONIBLE
    )


def _is_write_off_destination(destination: str) -> bool:
    """A return whose destination is a write-off ('Baja', 'merma') doesn't
    come back into sellable stock — everything else does."""
    normalized = destination.strip().lower()
    return "merma" in normalized or "baja" in normalized


# A damaged part can't go back into stock that gets sold to the next
# customer — only NUEVO can return to sellable inventory. USADO/DEFECTUOSO
# must be sent to a write-off destination.
CONDITIONS_REQUIRING_WRITE_OFF = {ReturnCondition.USADO, ReturnCondition.DEFECTUOSO}


class PartsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # Parts (catalog)

    async def list_parts(self, filial_id: uuid.UUID, search: str | None = None) -> list[PartRead]:
        from app.modules.warehouse.models import PartLot

        stock_total = (
            select(func.coalesce(func.sum(PartLot.quantity_remaining), 0))
            .where(PartLot.part_id == Part.id)
            .correlate(Part)
            .scalar_subquery()
        )
        fifo_cost = (
            select(PartLot.unit_cost)
            .where(PartLot.part_id == Part.id, PartLot.quantity_remaining > 0)
            .order_by(PartLot.received_at, PartLot.id)
            .limit(1)
            .correlate(Part)
            .scalar_subquery()
        )
        query = select(Part, stock_total, fifo_cost).where(Part.filial_id == filial_id)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(Part.code.ilike(term) | Part.name.ilike(term))

        result = await self.db.execute(query.order_by(Part.name))
        return [
            PartRead(
                id=part.id,
                filial_id=part.filial_id,
                code=part.code,
                name=part.name,
                category=part.category,
                brand=part.brand,
                application=part.application,
                unit=part.unit,
                stock_total=int(total),
                reference_price=round(float(cost) * 1.30, 2) if cost is not None else None,
                created_at=part.created_at,
                updated_at=part.updated_at,
            )
            for part, total, cost in result.all()
        ]

    async def get_latest_cost(self, part_id: uuid.UUID) -> float | None:
        from app.modules.warehouse.models import PartLot

        result = await self.db.execute(
            select(PartLot.unit_cost)
            .where(PartLot.part_id == part_id)
            .order_by(PartLot.received_at.desc(), PartLot.id.desc())
            .limit(1)
        )
        cost = result.scalar_one_or_none()
        return float(cost) if cost is not None else None

    async def get_reference_price(self, part_id: uuid.UUID) -> float:
        cost = await self.get_latest_cost(part_id)
        return round(cost * 1.30, 2) if cost is not None else 0.0

    async def get_part(self, part_id: uuid.UUID) -> Part:
        part = await self.db.get(Part, part_id)
        if part is None:
            raise PartNotFoundError(str(part_id))
        return part

    async def create_part(self, payload: PartCreate) -> Part:
        await self._ensure_code_available(payload.filial_id, payload.code)
        part = Part(
            filial_id=payload.filial_id,
            code=payload.code,
            name=payload.name,
            category=payload.category,
            brand=payload.brand,
            application=payload.application,
            unit=payload.unit,
            price=0,
            stock_quantity=0,
            min_stock=0,
        )
        _sync_availability(part)
        self.db.add(part)
        await self.db.commit()
        await self.db.refresh(part)
        return part

    async def update_part(self, part_id: uuid.UUID, payload: PartUpdate) -> Part:
        part = await self.get_part(part_id)
        if payload.code is not None and payload.code != part.code:
            await self._ensure_code_available(part.filial_id, payload.code)
        for field in ("code", "name", "category", "brand", "application", "unit"):
            value = getattr(payload, field)
            if value is not None:
                setattr(part, field, value)
        await self.db.commit()
        await self.db.refresh(part)
        return part

    async def _ensure_code_available(self, filial_id: uuid.UUID, code: str) -> None:
        result = await self.db.execute(
            select(Part).where(Part.filial_id == filial_id, Part.code == code)
        )
        if result.scalar_one_or_none() is not None:
            raise PartCodeAlreadyExistsError(code)

    async def bulk_create_parts(
        self, filial_id: uuid.UUID, items: list[PartBulkItem]
    ) -> tuple[list[Part], list[str]]:
        """Creates every item whose code isn't already taken. Duplicates (against the
        database or repeated within the same batch) are skipped, not rejected —
        bulk imports commonly re-upload the same file more than once."""
        existing_result = await self.db.execute(
            select(Part.code).where(Part.filial_id == filial_id)
        )
        existing_codes = {row[0] for row in existing_result.all()}

        created: list[Part] = []
        skipped: list[str] = []
        seen_in_batch: set[str] = set()

        for item in items:
            if item.code in existing_codes or item.code in seen_in_batch:
                skipped.append(item.code)
                continue
            part = Part(
                filial_id=filial_id,
                code=item.code,
                name=item.name,
                category=item.category,
                brand=item.brand,
                application=item.application,
                unit=item.unit,
                price=0,
                stock_quantity=0,
                min_stock=0,
            )
            _sync_availability(part)
            self.db.add(part)
            created.append(part)
            seen_in_batch.add(item.code)

        await self.db.commit()
        for part in created:
            await self.db.refresh(part)
        return created, skipped

    # Sales

    async def list_sales(self, filial_id: uuid.UUID, search: str | None = None) -> list[PartSale]:
        query = (
            select(PartSale)
            .options(selectinload(PartSale.lines))
            .where(PartSale.filial_id == filial_id)
            .order_by(PartSale.created_at.desc())
        )
        result = await self.db.execute(query)
        sales = list(result.scalars().all())
        if search:
            term = search.lower()
            sales = [s for s in sales if term in s.client_name.lower() or term in s.code.lower()]
        return sales

    async def get_sale(self, sale_id: uuid.UUID) -> PartSale:
        query = select(PartSale).options(selectinload(PartSale.lines)).where(PartSale.id == sale_id)
        result = await self.db.execute(query)
        sale = result.scalar_one_or_none()
        if sale is None:
            raise PartSaleNotFoundError(str(sale_id))
        return sale

    async def _sale_plan(self, payload, *, consume=False):
        warehouse = await self.db.get(Warehouse, payload.warehouse_id)
        if warehouse is None or warehouse.filial_id != payload.filial_id or not warehouse.is_active:
            raise BadRequestError("Selecciona un almacén activo de la filial.")
        if payload.discount_label not in PARTS_MULTIPLIERS:
            raise BadRequestError("Margen de venta inválido.")
        quantities = {}
        for line in payload.lines:
            quantities[line.part_id] = quantities.get(line.part_id, 0) + line.quantity
        plans = []
        # Stable lock ordering prevents deadlocks for sales with multiple parts.
        for part_id, quantity in sorted(quantities.items()):
            part = await self.get_part(part_id)
            if part.filial_id != payload.filial_id:
                raise BadRequestError("El repuesto no pertenece a la filial.")
            query = (
                select(PartLot)
                .where(
                    PartLot.warehouse_id == payload.warehouse_id,
                    PartLot.filial_id == payload.filial_id,
                    PartLot.part_id == part_id,
                    PartLot.quantity_remaining > 0,
                )
                .order_by(PartLot.received_at, PartLot.id)
            )
            if consume:
                query = query.with_for_update().execution_options(populate_existing=True)
            lots = list((await self.db.execute(query)).scalars().all())
            allocations = allocate_fifo(lots, quantity)
            cost, price, total = price_allocations(
                allocations, quantity, PARTS_MULTIPLIERS[payload.discount_label]
            )
            plans.append((part, quantity, allocations, cost, price, total))
        return plans

    async def quote_sale(self, payload):
        plans = await self._sale_plan(payload)
        return {
            "lines": [
                dict(
                    part_id=part.id,
                    warehouse_id=payload.warehouse_id,
                    quantity=quantity,
                    unit_cost=cost,
                    unit_price=price,
                    line_total=total,
                    allocations=[
                        dict(lot_id=lot.id, quantity=take, unit_cost=lot.unit_cost)
                        for lot, take in allocations
                    ],
                )
                for part, quantity, allocations, cost, price, total in plans
            ],
            "total": sum((plan[5] for plan in plans), Decimal(0)),
        }

    async def create_sale(self, payload: PartSaleCreate) -> PartSale:
        try:
            plans = await self._sale_plan(payload, consume=True)
            sale = PartSale(
                filial_id=payload.filial_id,
                client_name=payload.client_name,
                client_document=payload.client_document,
                request_reason="Venta de Repuestos",
                discount_label=payload.discount_label,
                sequence_number=await self._next_sale_sequence(payload.filial_id),
            )
            self.db.add(sale)
            await self.db.flush()
            for part, quantity, allocations, cost, price, total in plans:
                self.db.add(
                    PartSaleLine(
                        part_sale_id=sale.id,
                        part_id=part.id,
                        quantity=quantity,
                        warehouse_id=payload.warehouse_id,
                        unit_price=price,
                        unit_cost=cost,
                        line_total=total,
                        allocations=[
                            PartSaleLotAllocation(
                                lot_id=lot.id, quantity=take, unit_cost=lot.unit_cost
                            )
                            for lot, take in allocations
                        ],
                    )
                )
                for lot, take in allocations:
                    lot.quantity_remaining -= take
                    self.db.add(
                        StockMovement(
                            filial_id=payload.filial_id,
                            warehouse_id=payload.warehouse_id,
                            part_id=part.id,
                            movement_type=MovementType.SALIDA,
                            quantity=take,
                            unit_cost=lot.unit_cost,
                            reference=sale.code,
                        )
                    )
                part.stock_quantity = max(0, part.stock_quantity - quantity)
                _sync_availability(part)
            await self.db.commit()
            return await self.get_sale(sale.id)
        except Exception:
            await self.db.rollback()
            raise

    def _apply_dispatch(
        self, sale: PartSale, dispatched_lines: list[PartSaleLineDispatch] | None
    ) -> None:
        """Records what the almacenista actually pulled for each line and
        blocks the pendiente -> pedido transition if it doesn't match what
        was sold — stock was already deducted FIFO at sale creation, so a
        mismatch here means a real, unresolved inventory discrepancy."""
        lines_by_id = {line.id: line for line in sale.lines}
        if dispatched_lines is None or {d.line_id for d in dispatched_lines} != set(lines_by_id):
            raise DispatchQuantityRequiredError()

        for dispatch in dispatched_lines:
            lines_by_id[dispatch.line_id].dispatched_quantity = dispatch.dispatched_quantity

        mismatched = [
            str(line.part_id) for line in sale.lines if line.dispatched_quantity != line.quantity
        ]
        if mismatched:
            raise DispatchQuantityMismatchError(mismatched)

    async def _create_counter_warranties(self, sale: PartSale) -> None:
        """A part picked up at the counter without a workshop install still
        gets a warranty — on the part only, never labor — one row per lot
        allocation so a defective part can be traced back to the exact lot
        (and from there, the supplier) for a claim."""
        days_result = await self.db.execute(
            select(LaborSettings.part_warranty_days).where(LaborSettings.filial_id == sale.filial_id)
        )
        warranty_days = days_result.scalar_one_or_none() or DEFAULT_PART_WARRANTY_DAYS
        starts_at = datetime.now(timezone.utc)
        expires_at = starts_at + timedelta(days=warranty_days)
        for line in sale.lines:
            for allocation in line.allocations:
                self.db.add(
                    PartWarranty(
                        filial_id=sale.filial_id,
                        part_sale_line_id=line.id,
                        part_id=line.part_id,
                        lot_id=allocation.lot_id,
                        quantity=allocation.quantity,
                        warranty_days=warranty_days,
                        starts_at=starts_at,
                        expires_at=expires_at,
                    )
                )

    async def update_sale_status(
        self,
        sale_id: uuid.UUID,
        new_status: PartSaleStatus,
        dispatched_lines: list[PartSaleLineDispatch] | None = None,
    ) -> PartSale:
        # Serialize status transitions so cancellation restores allocations only once.
        await self.db.execute(
            select(PartSale)
            .where(PartSale.id == sale_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        sale = await self.get_sale(sale_id)
        if new_status != sale.status:
            if new_status not in SALE_TRANSITIONS.get(sale.status, set()):
                raise InvalidSaleStatusTransitionError(sale.status.value, new_status.value)

            if new_status == PartSaleStatus.PEDIDO:
                self._apply_dispatch(sale, dispatched_lines)

            sale.status = new_status

            if new_status == PartSaleStatus.CANCELADO:
                for line in sorted(sale.lines, key=lambda line: line.part_id):
                    for allocation in line.allocations:
                        lot = (
                            await self.db.execute(
                                select(PartLot)
                                .where(PartLot.id == allocation.lot_id)
                                .with_for_update()
                                .execution_options(populate_existing=True)
                            )
                        ).scalar_one()
                        lot.quantity_remaining += allocation.quantity
                        self.db.add(
                            StockMovement(
                                filial_id=sale.filial_id,
                                warehouse_id=line.warehouse_id,
                                part_id=line.part_id,
                                movement_type=MovementType.ENTRADA,
                                quantity=allocation.quantity,
                                unit_cost=allocation.unit_cost,
                                reference=sale.code,
                                note="Cancelación de venta",
                            )
                        )
                    if line.allocations:
                        part = await self.get_part(line.part_id)
                        part.stock_quantity += line.quantity
                        _sync_availability(part)

            if new_status == PartSaleStatus.COMPLETADO:
                from app.modules.administracion.enums import MovementSourceType
                from app.modules.administracion.service import AdministracionService

                admin_service = AdministracionService(self.db)
                await admin_service.record_automatic_income(
                    sale.filial_id,
                    f"Cierre de venta de repuestos · {sale.client_name}",
                    sale.total,
                    sale.code,
                    source_type=MovementSourceType.PART_SALE,
                    source_id=sale.id,
                )
                await self._create_counter_warranties(sale)

        await self.db.commit()
        return await self.get_sale(sale.id)

    async def _next_sale_sequence(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(PartSale.sequence_number)).where(PartSale.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 5000) + 1

    # Returns

    async def list_returns(self, filial_id: uuid.UUID) -> list[PartReturn]:
        result = await self.db.execute(
            select(PartReturn)
            .where(PartReturn.filial_id == filial_id)
            .order_by(PartReturn.created_at.desc())
        )
        return list(result.scalars().all())

    async def create_return(
        self, payload: PartReturnCreate, responsible_user_id: uuid.UUID
    ) -> PartReturn:
        part = await self.get_part(payload.part_id)
        is_write_off = _is_write_off_destination(payload.destination_warehouse)

        if payload.condition in CONDITIONS_REQUIRING_WRITE_OFF and not is_write_off:
            raise InvalidReturnDestinationError(payload.condition.value)
        if not payload.photo_urls:
            raise MissingReturnPhotoError()

        ret = PartReturn(
            filial_id=payload.filial_id,
            part_id=payload.part_id,
            condition=payload.condition,
            origin_warehouse=payload.origin_warehouse,
            destination_warehouse=payload.destination_warehouse,
            quantity=payload.quantity,
            reason=payload.reason,
            reason_notes=payload.reason_notes,
            responsible_user_id=responsible_user_id,
            photo_urls=payload.photo_urls,
        )
        self.db.add(ret)

        if not is_write_off:
            part.stock_quantity += payload.quantity
            _sync_availability(part)

        await self.db.commit()
        await self.db.refresh(ret)
        return ret
