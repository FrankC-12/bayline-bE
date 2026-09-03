import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.parts.enums import PartSaleStatus
from app.modules.parts.exceptions import (
    InvalidSaleStatusTransitionError,
    PartCodeAlreadyExistsError,
    PartNotFoundError,
    PartSaleNotFoundError,
)
from app.modules.parts.models import Part, PartReturn, PartSale, PartSaleLine
from app.modules.parts.schemas import (
    PartBulkItem,
    PartCreate,
    PartRead,
    PartReturnCreate,
    PartSaleCreate,
    PartUpdate,
)

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
        latest_cost = (
            select(PartLot.unit_cost)
            .where(PartLot.part_id == Part.id)
            .order_by(PartLot.received_at.desc(), PartLot.id.desc())
            .limit(1)
            .correlate(Part)
            .scalar_subquery()
        )
        query = select(Part, stock_total, latest_cost).where(Part.filial_id == filial_id)
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
        result = await self.db.execute(select(Part).where(Part.filial_id == filial_id, Part.code == code))
        if result.scalar_one_or_none() is not None:
            raise PartCodeAlreadyExistsError(code)

    async def bulk_create_parts(
        self, filial_id: uuid.UUID, items: list[PartBulkItem]
    ) -> tuple[list[Part], list[str]]:
        """Creates every item whose code isn't already taken. Duplicates (against the
        database or repeated within the same batch) are skipped, not rejected —
        bulk imports commonly re-upload the same file more than once."""
        existing_result = await self.db.execute(select(Part.code).where(Part.filial_id == filial_id))
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

    async def create_sale(self, payload: PartSaleCreate) -> PartSale:
        next_seq = await self._next_sale_sequence(payload.filial_id)
        sale = PartSale(
            filial_id=payload.filial_id,
            client_name=payload.client_name,
            client_document=payload.client_document,
            request_reason="Venta de Repuestos",
            discount_label=payload.discount_label,
            sequence_number=next_seq,
        )
        self.db.add(sale)
        await self.db.flush()

        for line in payload.lines:
            part = await self.get_part(line.part_id)
            cost_snapshot = await self.get_latest_cost(part.id)
            self.db.add(
                PartSaleLine(
                    part_sale_id=sale.id,
                    part_id=part.id,
                    quantity=line.quantity,
                    unit_price=round(cost_snapshot * 1.30, 2) if cost_snapshot is not None else 0.0,
                    unit_cost=cost_snapshot,
                )
            )
            part.stock_quantity = max(0, part.stock_quantity - line.quantity)
            _sync_availability(part)

        await self.db.commit()
        return await self.get_sale(sale.id)

    async def update_sale_status(self, sale_id: uuid.UUID, new_status: PartSaleStatus) -> PartSale:
        sale = await self.get_sale(sale_id)
        if new_status != sale.status:
            if new_status not in SALE_TRANSITIONS.get(sale.status, set()):
                raise InvalidSaleStatusTransitionError(sale.status.value, new_status.value)
            sale.status = new_status

            if new_status == PartSaleStatus.COMPLETADO:
                from app.modules.administracion.service import AdministracionService

                admin_service = AdministracionService(self.db)
                await admin_service.record_automatic_income(
                    sale.filial_id,
                    f"Cierre de venta de repuestos · {sale.client_name}",
                    sale.total,
                    sale.code,
                )

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

    async def create_return(self, payload: PartReturnCreate, responsible_user_id: uuid.UUID) -> PartReturn:
        part = await self.get_part(payload.part_id)
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
        )
        self.db.add(ret)

        if not _is_write_off_destination(payload.destination_warehouse):
            part.stock_quantity += payload.quantity
            _sync_availability(part)

        await self.db.commit()
        await self.db.refresh(ret)
        return ret
