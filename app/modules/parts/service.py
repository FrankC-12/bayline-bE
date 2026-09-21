import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

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
    PartCategoryNameAlreadyExistsError,
    PartCategoryNotFoundError,
    PartCodeAlreadyExistsError,
    PartMeasureNameAlreadyExistsError,
    PartMeasureNotFoundError,
    PartNotFoundError,
    PartSaleNotFoundError,
    VehicleModelBrandMismatchError,
)
from app.modules.parts.models import (
    Part,
    PartCategory,
    PartMeasure,
    PartReturn,
    PartSale,
    PartSaleLine,
    PartSaleLotAllocation,
    PartWarranty,
)
from app.modules.parts.pricing import PARTS_MULTIPLIERS
from app.modules.parts.schemas import (
    PartBulkItem,
    PartCategoryCreate,
    PartCategoryUpdate,
    PartCreate,
    PartMeasureCreate,
    PartMeasureUpdate,
    PartRead,
    PartReturnCreate,
    PartSaleCreate,
    PartSaleLineDispatch,
    PartUpdate,
)
from app.modules.post_ventas.models import LaborSettings
from app.modules.vehicle_catalog.models import VehicleBrand, VehicleModel
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

    async def _holding_id_for_filial(self, filial_id: uuid.UUID) -> uuid.UUID:
        from app.modules.filiales.models import Filial

        filial = await self.db.get(Filial, filial_id)
        if filial is None:
            raise BadRequestError("La filial no existe.")
        return filial.holding_id

    async def _validate_vehicle_fit(
        self,
        vehicle_brand_id: uuid.UUID | None,
        vehicle_model_id: uuid.UUID | None,
        year_from: int | None,
        year_to: int | None,
    ) -> None:
        if vehicle_model_id is not None:
            if vehicle_brand_id is None:
                raise VehicleModelBrandMismatchError()
            model = await self.db.get(VehicleModel, vehicle_model_id)
            if model is None or model.brand_id != vehicle_brand_id:
                raise VehicleModelBrandMismatchError()
        if year_from is not None and year_to is not None and year_from > year_to:
            raise BadRequestError("El año 'desde' no puede ser mayor al año 'hasta'.")

    async def _parts_to_read(
        self, rows: list[tuple[Part, int, float | None, str | None]]
    ) -> list[PartRead]:
        """Batch-resolves category/vehicle-brand/vehicle-model/measure names for
        a page of parts in a handful of queries, instead of one lookup per part."""
        category_ids = {p.category_id for p, _, _, _ in rows}
        brand_ids = {p.vehicle_brand_id for p, _, _, _ in rows if p.vehicle_brand_id}
        model_ids = {p.vehicle_model_id for p, _, _, _ in rows if p.vehicle_model_id}
        measure_ids = {p.measure_id for p, _, _, _ in rows if p.measure_id}

        categories: dict[uuid.UUID, str] = {}
        if category_ids:
            result = await self.db.execute(
                select(PartCategory.id, PartCategory.name).where(PartCategory.id.in_(category_ids))
            )
            categories = {row.id: row.name for row in result.all()}

        brands: dict[uuid.UUID, str] = {}
        if brand_ids:
            result = await self.db.execute(
                select(VehicleBrand.id, VehicleBrand.name).where(VehicleBrand.id.in_(brand_ids))
            )
            brands = {row.id: row.name for row in result.all()}

        models: dict[uuid.UUID, str] = {}
        if model_ids:
            result = await self.db.execute(
                select(VehicleModel.id, VehicleModel.name).where(VehicleModel.id.in_(model_ids))
            )
            models = {row.id: row.name for row in result.all()}

        measures: dict[uuid.UUID, str] = {}
        if measure_ids:
            result = await self.db.execute(
                select(PartMeasure.id, PartMeasure.name).where(PartMeasure.id.in_(measure_ids))
            )
            measures = {row.id: row.name for row in result.all()}

        return [
            PartRead(
                id=part.id,
                filial_id=part.filial_id,
                code=part.code,
                manufacturer_part_number=part.manufacturer_part_number,
                name=part.name,
                category_id=part.category_id,
                category_name=categories.get(part.category_id, ""),
                vehicle_brand_id=part.vehicle_brand_id,
                vehicle_brand_name=brands.get(part.vehicle_brand_id) if part.vehicle_brand_id else None,
                vehicle_model_id=part.vehicle_model_id,
                vehicle_model_name=models.get(part.vehicle_model_id) if part.vehicle_model_id else None,
                year_from=part.year_from,
                year_to=part.year_to,
                measure_id=part.measure_id,
                measure_name=measures.get(part.measure_id) if part.measure_id else None,
                unit=part.unit,
                min_stock=part.min_stock,
                is_active=part.is_active,
                stock_total=int(total),
                reference_price=round(float(cost) * 1.30, 2) if cost is not None else None,
                latest_cost=float(cost) if cost is not None else None,
                location=location,
                created_at=part.created_at,
                updated_at=part.updated_at,
            )
            for part, total, cost, location in rows
        ]

    async def _part_to_read(self, part: Part) -> PartRead:
        stock_total = await self._stock_total(part.id)
        latest_cost = await self.get_latest_cost(part.id)
        reference_price = round(latest_cost * 1.30, 2) if latest_cost is not None else 0.0
        location = await self.get_latest_location(part.id)
        return (await self._parts_to_read([(part, stock_total, None, location)]))[0].model_copy(
            update={"reference_price": reference_price, "latest_cost": latest_cost}
        )

    async def _stock_total(self, part_id: uuid.UUID) -> int:
        from app.modules.warehouse.models import PartLot

        result = await self.db.execute(
            select(func.coalesce(func.sum(PartLot.quantity_remaining), 0)).where(
                PartLot.part_id == part_id
            )
        )
        return int(result.scalar_one())

    async def get_latest_location(self, part_id: uuid.UUID) -> str | None:
        """Ubicación en almacén — jalada del lote recibido más recientemente
        que registró una (across every warehouse, same convention as
        stock_total/reference_price today)."""
        from app.modules.warehouse.models import PartLot

        result = await self.db.execute(
            select(PartLot.location)
            .where(PartLot.part_id == part_id, PartLot.location.isnot(None))
            .order_by(PartLot.received_at.desc(), PartLot.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_parts(
        self, filial_id: uuid.UUID, search: str | None = None, include_inactive: bool = False
    ) -> list[PartRead]:
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
        latest_location = (
            select(PartLot.location)
            .where(PartLot.part_id == Part.id, PartLot.location.isnot(None))
            .order_by(PartLot.received_at.desc(), PartLot.id.desc())
            .limit(1)
            .correlate(Part)
            .scalar_subquery()
        )
        query = select(Part, stock_total, fifo_cost, latest_location).where(Part.filial_id == filial_id)
        if not include_inactive:
            query = query.where(Part.is_active.is_(True))
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                Part.code.ilike(term)
                | Part.name.ilike(term)
                | Part.manufacturer_part_number.ilike(term)
            )

        result = await self.db.execute(query.order_by(Part.name))
        rows = [
            (part, int(total), float(cost) if cost is not None else None, location)
            for part, total, cost, location in result.all()
        ]
        return await self._parts_to_read(rows)

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

    async def get_part(self, part_id: uuid.UUID) -> Part:
        part = await self.db.get(Part, part_id)
        if part is None:
            raise PartNotFoundError(str(part_id))
        return part

    async def create_part(self, payload: PartCreate) -> PartRead:
        await self._ensure_code_available(payload.filial_id, payload.code)
        holding_id = await self._holding_id_for_filial(payload.filial_id)
        await self._get_category(payload.category_id, holding_id)
        await self._validate_vehicle_fit(
            payload.vehicle_brand_id, payload.vehicle_model_id, payload.year_from, payload.year_to
        )
        part = Part(
            filial_id=payload.filial_id,
            code=payload.code,
            manufacturer_part_number=payload.manufacturer_part_number,
            name=payload.name,
            category_id=payload.category_id,
            vehicle_brand_id=payload.vehicle_brand_id,
            vehicle_model_id=payload.vehicle_model_id,
            year_from=payload.year_from,
            year_to=payload.year_to,
            measure_id=payload.measure_id,
            unit=payload.unit,
            min_stock=payload.min_stock,
            price=0,
            stock_quantity=0,
        )
        _sync_availability(part)
        self.db.add(part)
        await self.db.commit()
        await self.db.refresh(part)
        return await self._part_to_read(part)

    async def update_part(self, part_id: uuid.UUID, payload: PartUpdate) -> PartRead:
        part = await self.get_part(part_id)
        if payload.code is not None and payload.code != part.code:
            await self._ensure_code_available(part.filial_id, payload.code)
        if payload.category_id is not None:
            holding_id = await self._holding_id_for_filial(part.filial_id)
            await self._get_category(payload.category_id, holding_id)
            part.category_id = payload.category_id

        if payload.clear_vehicle_brand:
            part.vehicle_brand_id = None
        elif payload.vehicle_brand_id is not None:
            part.vehicle_brand_id = payload.vehicle_brand_id

        if payload.clear_vehicle_model:
            part.vehicle_model_id = None
        elif payload.vehicle_model_id is not None:
            part.vehicle_model_id = payload.vehicle_model_id

        if payload.clear_measure:
            part.measure_id = None
        elif payload.measure_id is not None:
            part.measure_id = payload.measure_id

        if payload.clear_manufacturer_part_number:
            part.manufacturer_part_number = None
        elif payload.manufacturer_part_number is not None:
            part.manufacturer_part_number = payload.manufacturer_part_number

        if payload.clear_years:
            part.year_from = None
            part.year_to = None
        else:
            if payload.year_from is not None:
                part.year_from = payload.year_from
            if payload.year_to is not None:
                part.year_to = payload.year_to

        await self._validate_vehicle_fit(part.vehicle_brand_id, part.vehicle_model_id, part.year_from, part.year_to)

        for field in ("code", "name", "unit", "min_stock"):
            value = getattr(payload, field)
            if value is not None:
                setattr(part, field, value)

        await self.db.commit()
        await self.db.refresh(part)
        return await self._part_to_read(part)

    async def set_part_active(self, part_id: uuid.UUID, is_active: bool) -> PartRead:
        part = await self.get_part(part_id)
        part.is_active = is_active
        await self.db.commit()
        await self.db.refresh(part)
        return await self._part_to_read(part)

    async def _ensure_code_available(self, filial_id: uuid.UUID, code: str) -> None:
        result = await self.db.execute(
            select(Part).where(Part.filial_id == filial_id, Part.code == code)
        )
        if result.scalar_one_or_none() is not None:
            raise PartCodeAlreadyExistsError(code)

    async def bulk_create_parts(
        self, filial_id: uuid.UUID, items: list[PartBulkItem]
    ) -> tuple[list[PartRead], list[str]]:
        """Creates every item whose code isn't already taken. Duplicates (against the
        database or repeated within the same batch) are skipped, not rejected —
        bulk imports commonly re-upload the same file more than once. `category`
        is resolved by name against the holding's catalog, auto-creating it if
        it doesn't exist yet — bulk import stays a flat, simple format."""
        holding_id = await self._holding_id_for_filial(filial_id)
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
            category = await self.get_or_create_category(holding_id, item.category)
            part = Part(
                filial_id=filial_id,
                code=item.code,
                name=item.name,
                category_id=category.id,
                unit=item.unit,
                price=0,
                stock_quantity=0,
            )
            _sync_availability(part)
            self.db.add(part)
            created.append(part)
            seen_in_batch.add(item.code)

        await self.db.commit()
        for part in created:
            await self.db.refresh(part)
        return [await self._part_to_read(part) for part in created], skipped

    # Part categories (Ajustes → Categorías de Repuestos) — holding-wide.

    async def list_categories(
        self, holding_id: uuid.UUID, include_inactive: bool = False
    ) -> list[PartCategory]:
        query = select(PartCategory).where(PartCategory.holding_id == holding_id).order_by(PartCategory.name)
        if not include_inactive:
            query = query.where(PartCategory.is_active.is_(True))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _get_category(self, category_id: uuid.UUID, holding_id: uuid.UUID) -> PartCategory:
        result = await self.db.execute(
            select(PartCategory).where(PartCategory.id == category_id, PartCategory.holding_id == holding_id)
        )
        category = result.scalar_one_or_none()
        if category is None:
            raise PartCategoryNotFoundError(str(category_id))
        return category

    async def get_or_create_category(self, holding_id: uuid.UUID, name: str) -> PartCategory:
        result = await self.db.execute(
            select(PartCategory).where(
                PartCategory.holding_id == holding_id, func.lower(PartCategory.name) == name.strip().lower()
            )
        )
        category = result.scalar_one_or_none()
        if category is not None:
            return category
        category = PartCategory(holding_id=holding_id, name=name.strip())
        self.db.add(category)
        await self.db.flush()
        return category

    async def create_category(self, holding_id: uuid.UUID, payload: PartCategoryCreate) -> PartCategory:
        await self._ensure_category_name_is_available(holding_id, payload.name)
        category = PartCategory(holding_id=holding_id, name=payload.name.strip())
        self.db.add(category)
        await self.db.commit()
        await self.db.refresh(category)
        return category

    async def update_category(
        self, category_id: uuid.UUID, holding_id: uuid.UUID, payload: PartCategoryUpdate
    ) -> PartCategory:
        category = await self._get_category(category_id, holding_id)
        if payload.name and payload.name.strip() != category.name:
            await self._ensure_category_name_is_available(holding_id, payload.name)
            category.name = payload.name.strip()
        await self.db.commit()
        await self.db.refresh(category)
        return category

    async def set_category_active(
        self, category_id: uuid.UUID, holding_id: uuid.UUID, is_active: bool
    ) -> PartCategory:
        category = await self._get_category(category_id, holding_id)
        category.is_active = is_active
        await self.db.commit()
        await self.db.refresh(category)
        return category

    async def _ensure_category_name_is_available(self, holding_id: uuid.UUID, name: str) -> None:
        result = await self.db.execute(
            select(PartCategory).where(
                PartCategory.holding_id == holding_id, func.lower(PartCategory.name) == name.strip().lower()
            )
        )
        if result.scalar_one_or_none() is not None:
            raise PartCategoryNameAlreadyExistsError(name)

    # Part measures (Ajustes → Medidas de Repuestos) — holding-wide.

    async def list_measures(
        self, holding_id: uuid.UUID, include_inactive: bool = False
    ) -> list[PartMeasure]:
        query = select(PartMeasure).where(PartMeasure.holding_id == holding_id).order_by(PartMeasure.name)
        if not include_inactive:
            query = query.where(PartMeasure.is_active.is_(True))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def _get_measure(self, measure_id: uuid.UUID, holding_id: uuid.UUID) -> PartMeasure:
        result = await self.db.execute(
            select(PartMeasure).where(PartMeasure.id == measure_id, PartMeasure.holding_id == holding_id)
        )
        measure = result.scalar_one_or_none()
        if measure is None:
            raise PartMeasureNotFoundError(str(measure_id))
        return measure

    async def create_measure(self, holding_id: uuid.UUID, payload: PartMeasureCreate) -> PartMeasure:
        await self._ensure_measure_name_is_available(holding_id, payload.name)
        measure = PartMeasure(holding_id=holding_id, name=payload.name.strip())
        self.db.add(measure)
        await self.db.commit()
        await self.db.refresh(measure)
        return measure

    async def update_measure(
        self, measure_id: uuid.UUID, holding_id: uuid.UUID, payload: PartMeasureUpdate
    ) -> PartMeasure:
        measure = await self._get_measure(measure_id, holding_id)
        if payload.name and payload.name.strip() != measure.name:
            await self._ensure_measure_name_is_available(holding_id, payload.name)
            measure.name = payload.name.strip()
        await self.db.commit()
        await self.db.refresh(measure)
        return measure

    async def set_measure_active(
        self, measure_id: uuid.UUID, holding_id: uuid.UUID, is_active: bool
    ) -> PartMeasure:
        measure = await self._get_measure(measure_id, holding_id)
        measure.is_active = is_active
        await self.db.commit()
        await self.db.refresh(measure)
        return measure

    async def _ensure_measure_name_is_available(self, holding_id: uuid.UUID, name: str) -> None:
        result = await self.db.execute(
            select(PartMeasure).where(
                PartMeasure.holding_id == holding_id, func.lower(PartMeasure.name) == name.strip().lower()
            )
        )
        if result.scalar_one_or_none() is not None:
            raise PartMeasureNameAlreadyExistsError(name)

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

    async def _tax_breakdown(
        self, filial_id: uuid.UUID, subtotal: Decimal
    ) -> tuple[float, Decimal, float, Decimal]:
        """IVA on the subtotal, IGTF on (subtotal + IVA) — never on the
        subtotal alone. Same formula already used for service orders and
        vehicle sales; a counter sale is assumed paid in foreign currency in
        full, the same simplifying assumption a vehicle's own list-price
        preview already makes (no partial/Bs payment tracking exists for a
        counter sale, unlike an ODS invoice or a vehicle checkout)."""
        result = await self.db.execute(
            select(LaborSettings.iva_percentage, LaborSettings.igtf_percentage).where(
                LaborSettings.filial_id == filial_id
            )
        )
        row = result.one_or_none()
        iva_percentage = float(row[0]) if row else 16.0
        igtf_percentage = float(row[1]) if row else 3.0
        cent = Decimal("0.01")
        iva_amount = (subtotal * Decimal(str(iva_percentage)) / 100).quantize(cent, rounding=ROUND_HALF_UP)
        igtf_amount = ((subtotal + iva_amount) * Decimal(str(igtf_percentage)) / 100).quantize(
            cent, rounding=ROUND_HALF_UP
        )
        return iva_percentage, iva_amount, igtf_percentage, igtf_amount

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
            allocations = allocate_fifo(lots, quantity, part_id=part.id, part_name=part.name)
            cost, price, total = price_allocations(
                allocations, quantity, PARTS_MULTIPLIERS[payload.discount_label]
            )
            plans.append((part, quantity, allocations, cost, price, total))
        return plans

    async def quote_sale(self, payload):
        plans = await self._sale_plan(payload)
        subtotal = sum((plan[5] for plan in plans), Decimal(0))
        iva_percentage, iva_amount, igtf_percentage, igtf_amount = await self._tax_breakdown(
            payload.filial_id, subtotal
        )
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
            "total": subtotal,
            "iva_percentage": iva_percentage,
            "iva_amount": iva_amount,
            "igtf_percentage": igtf_percentage,
            "igtf_amount": igtf_amount,
            "total_with_taxes": subtotal + iva_amount + igtf_amount,
        }

    async def create_sale(
        self, payload: PartSaleCreate, responsible_user_id: uuid.UUID | None = None
    ) -> PartSale:
        try:
            plans = await self._sale_plan(payload, consume=True)
            subtotal = sum((plan[5] for plan in plans), Decimal(0))
            iva_percentage, iva_amount, igtf_percentage, igtf_amount = await self._tax_breakdown(
                payload.filial_id, subtotal
            )
            sale = PartSale(
                filial_id=payload.filial_id,
                client_name=payload.client_name,
                client_document=payload.client_document,
                request_reason="Venta de Repuestos",
                discount_label=payload.discount_label,
                sequence_number=await self._next_sale_sequence(payload.filial_id),
                iva_percentage=iva_percentage,
                iva_amount=iva_amount,
                igtf_percentage=igtf_percentage,
                igtf_amount=igtf_amount,
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
                            responsible_user_id=responsible_user_id,
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
        responsible_user_id: uuid.UUID | None = None,
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
                                responsible_user_id=responsible_user_id,
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

    async def _next_return_lot_number(self, filial_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.max(PartLot.lot_number)).where(PartLot.filial_id == filial_id)
        )
        current_max = result.scalar()
        return (current_max or 100) + 1

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
            # A return that comes back into sellable stock has to move the
            # same three things a normal entrada does — a PartLot (what the
            # displayed stock and FIFO cost are actually computed from), a
            # StockMovement ledger entry, and the Part.stock_quantity
            # convenience column — otherwise the part's shown quantity never
            # moves even though the return "succeeded".
            warehouse_result = await self.db.execute(
                select(Warehouse).where(
                    Warehouse.filial_id == payload.filial_id,
                    func.lower(Warehouse.name) == payload.destination_warehouse.strip().lower(),
                )
            )
            warehouse = warehouse_result.scalar_one_or_none()
            if warehouse is None:
                raise BadRequestError(
                    f"El almacén de destino '{payload.destination_warehouse}' no existe."
                )

            unit_cost = await self.get_latest_cost(payload.part_id) or 0.0
            self.db.add(
                PartLot(
                    filial_id=payload.filial_id,
                    lot_number=await self._next_return_lot_number(payload.filial_id),
                    warehouse_id=warehouse.id,
                    part_id=payload.part_id,
                    quantity_received=payload.quantity,
                    quantity_remaining=payload.quantity,
                    unit_cost=unit_cost,
                    note="Devolución de repuesto",
                )
            )
            self.db.add(
                StockMovement(
                    filial_id=payload.filial_id,
                    warehouse_id=warehouse.id,
                    part_id=payload.part_id,
                    movement_type=MovementType.ENTRADA,
                    quantity=payload.quantity,
                    unit_cost=unit_cost,
                    note="Devolución de repuesto",
                    responsible_user_id=responsible_user_id,
                )
            )
            part.stock_quantity += payload.quantity
            _sync_availability(part)

        await self.db.commit()
        await self.db.refresh(ret)
        return ret
