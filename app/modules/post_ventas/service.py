import calendar
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError
from app.modules.exchange_rates.models import ExchangeRate
from app.modules.post_ventas.enums import CATEGORY_PREFIXES, TemparioCategory, VehicleWarrantySource
from app.modules.post_ventas.exceptions import (
    MaintenancePlanNotFoundError,
    TemparioCodeAlreadyExistsError,
    TemparioNotFoundError,
    VehicleWarrantyAlreadyExistsError,
    VehicleWarrantyNotFoundError,
)
from app.modules.post_ventas.models import (
    LaborSettings,
    MaintenancePlan,
    MaintenancePlanEntry,
    Tempario,
    TemparioPart,
    VehicleWarranty,
    WorkshopWarranty,
)
from app.modules.post_ventas.schemas import (
    CompatibleVehicle,
    LaborSettingsUpdate,
    MaintenancePlanCreate,
    MaintenancePlanEntryRead,
    MaintenancePlanRead,
    MaintenancePlanUpdate,
    TemparioCreate,
    TemparioPartRead,
    TemparioRead,
    TemparioUpdate,
    VehicleWarrantyBulkItem,
    VehicleWarrantyCreate,
    VehicleWarrantyRead,
    WorkshopWarrantyRead,
)

PARTS_MARGIN_RATE = 0.30


def _tempario_to_read(t: Tempario, hourly_rate: float) -> TemparioRead:
    parts_cost = sum(p.quantity * float(p.unit_cost) for p in t.parts)
    parts_margin = parts_cost * PARTS_MARGIN_RATE
    labor_cost = float(t.estimated_hours) * hourly_rate
    total_price = parts_cost + parts_margin + labor_cost

    return TemparioRead(
        id=t.id,
        filial_id=t.filial_id,
        code=t.code,
        category=t.category,
        name=t.name,
        estimated_hours=float(t.estimated_hours),
        year_from=t.year_from,
        year_to=t.year_to,
        compatible_vehicles=[CompatibleVehicle(**v) for v in t.compatible_vehicles],
        tools=list(t.tools),
        requires_parts=t.requires_parts,
        parts=[
            TemparioPartRead(
                id=p.id,
                part_id=p.part_id,
                name=p.name,
                quantity=p.quantity,
                unit_cost=float(p.unit_cost),
                subtotal=p.quantity * float(p.unit_cost),
            )
            for p in t.parts
        ],
        parts_cost=parts_cost,
        parts_margin=parts_margin,
        labor_cost=labor_cost,
        total_price=total_price,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _plan_entry_to_read(entry: MaintenancePlanEntry) -> MaintenancePlanEntryRead:
    return MaintenancePlanEntryRead(
        id=entry.id,
        tempario_id=entry.tempario_id,
        tempario_code=entry.tempario.code,
        tempario_name=entry.tempario.name,
        interval_km=entry.interval_km,
        interval_months=entry.interval_months,
    )


def _plan_to_read(plan: MaintenancePlan) -> MaintenancePlanRead:
    # Nearest-due first: by km, falling back to months for entries with no km.
    entries = sorted(
        plan.entries,
        key=lambda e: (
            e.interval_km if e.interval_km is not None else 10**9,
            e.interval_months if e.interval_months is not None else 10**9,
        ),
    )
    return MaintenancePlanRead(
        id=plan.id,
        filial_id=plan.filial_id,
        brand=plan.brand,
        name=plan.name,
        entries=[_plan_entry_to_read(e) for e in entries],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


class PostVentasService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # Labor settings

    async def get_labor_settings(self, filial_id: uuid.UUID) -> LaborSettings:
        result = await self.db.execute(select(LaborSettings).where(LaborSettings.filial_id == filial_id))
        settings = result.scalar_one_or_none()
        changed = False
        if settings is None:
            settings = LaborSettings(filial_id=filial_id)
            self.db.add(settings)
            changed = True

        latest_usd = await self.db.execute(
            select(ExchangeRate)
            .where(ExchangeRate.currency == "USD")
            .order_by(ExchangeRate.value_date.desc())
            .limit(1)
        )
        rate_row = latest_usd.scalar_one_or_none()
        if rate_row is not None and (
            settings.bcv_rate_date is None or rate_row.value_date > settings.bcv_rate_date
        ):
            settings.bcv_rate = rate_row.rate_ves
            settings.bcv_rate_date = rate_row.value_date
            changed = True

        if changed:
            await self.db.commit()
            await self.db.refresh(settings)
        return settings

    async def update_labor_settings(
        self, filial_id: uuid.UUID, payload: LaborSettingsUpdate
    ) -> LaborSettings:
        settings = await self.get_labor_settings(filial_id)
        settings.hourly_rate = payload.hourly_rate
        settings.commission_percentage = payload.commission_percentage
        settings.igtf_percentage = payload.igtf_percentage
        settings.iva_percentage = payload.iva_percentage
        settings.part_warranty_days = payload.part_warranty_days
        settings.vehicle_warranty_default_months = payload.vehicle_warranty_default_months
        settings.workshop_warranty_days = payload.workshop_warranty_days
        settings.workshop_warranty_km = payload.workshop_warranty_km
        settings.workshop_parts_warranty_days = payload.workshop_parts_warranty_days
        settings.workshop_parts_warranty_km = payload.workshop_parts_warranty_km
        settings.manual_movement_attachment_threshold_usd = payload.manual_movement_attachment_threshold_usd
        settings.iva_retention_default_percentage = payload.iva_retention_default_percentage
        settings.islr_retention_default_percentage = payload.islr_retention_default_percentage
        if payload.bcv_rate is not None:
            settings.bcv_rate = payload.bcv_rate
            settings.bcv_rate_date = date.today()
        await self.db.commit()
        await self.db.refresh(settings)
        return settings

    # Temparios

    async def _get_tempario_model(self, tempario_id: uuid.UUID) -> Tempario:
        query = select(Tempario).options(selectinload(Tempario.parts)).where(Tempario.id == tempario_id)
        result = await self.db.execute(query)
        tempario = result.scalar_one_or_none()
        if tempario is None:
            raise TemparioNotFoundError(str(tempario_id))
        return tempario

    async def list_temparios(self, filial_id: uuid.UUID, search: str | None = None) -> list[TemparioRead]:
        query = (
            select(Tempario)
            .options(selectinload(Tempario.parts))
            .where(Tempario.filial_id == filial_id)
            .order_by(Tempario.category, Tempario.sequence_number)
        )
        result = await self.db.execute(query)
        temparios = list(result.scalars().all())

        if search:
            term = search.lower()
            temparios = [t for t in temparios if term in t.name.lower() or term in t.code.lower()]

        settings = await self.get_labor_settings(filial_id)
        rate = float(settings.hourly_rate)
        return [_tempario_to_read(t, rate) for t in temparios]

    async def get_tempario(self, tempario_id: uuid.UUID) -> TemparioRead:
        t = await self._get_tempario_model(tempario_id)
        settings = await self.get_labor_settings(t.filial_id)
        return _tempario_to_read(t, float(settings.hourly_rate))

    async def create_tempario(self, payload: TemparioCreate) -> TemparioRead:
        sequence_number = payload.sequence_number or await self._next_sequence(
            payload.filial_id, payload.category
        )
        await self._ensure_code_available(payload.filial_id, payload.category, sequence_number)

        t = Tempario(
            filial_id=payload.filial_id,
            category=payload.category,
            sequence_number=sequence_number,
            name=payload.name,
            estimated_hours=payload.estimated_hours,
            year_from=payload.year_from,
            year_to=payload.year_to,
            compatible_vehicles=[v.model_dump() for v in payload.compatible_vehicles],
            tools=payload.tools,
            requires_parts=payload.requires_parts,
        )
        self.db.add(t)
        await self.db.flush()

        for part in payload.parts:
            self.db.add(
                TemparioPart(
                    tempario_id=t.id,
                    part_id=part.part_id,
                    name=part.name,
                    quantity=part.quantity,
                    unit_cost=part.unit_cost,
                )
            )

        await self.db.commit()
        return await self.get_tempario(t.id)

    async def update_tempario(self, tempario_id: uuid.UUID, payload: TemparioUpdate) -> TemparioRead:
        t = await self._get_tempario_model(tempario_id)

        if payload.name is not None:
            t.name = payload.name
        if payload.estimated_hours is not None:
            t.estimated_hours = payload.estimated_hours
        if payload.year_from is not None:
            t.year_from = payload.year_from
        if payload.year_to is not None:
            t.year_to = payload.year_to
        if payload.compatible_vehicles is not None:
            t.compatible_vehicles = [v.model_dump() for v in payload.compatible_vehicles]
        if payload.tools is not None:
            t.tools = payload.tools
        if payload.requires_parts is not None:
            t.requires_parts = payload.requires_parts

        if payload.parts is not None:
            for existing in list(t.parts):
                await self.db.delete(existing)
            await self.db.flush()
            for part in payload.parts:
                self.db.add(
                    TemparioPart(
                        tempario_id=t.id,
                        part_id=part.part_id,
                        name=part.name,
                        quantity=part.quantity,
                        unit_cost=part.unit_cost,
                    )
                )

        await self.db.commit()
        return await self.get_tempario(tempario_id)

    async def _next_sequence(self, filial_id: uuid.UUID, category: TemparioCategory) -> int:
        result = await self.db.execute(
            select(func.max(Tempario.sequence_number)).where(
                Tempario.filial_id == filial_id, Tempario.category == category
            )
        )
        current_max = result.scalar()
        return (current_max or 500) + 1

    async def _ensure_code_available(
        self, filial_id: uuid.UUID, category: TemparioCategory, sequence_number: int
    ) -> None:
        result = await self.db.execute(
            select(Tempario).where(
                Tempario.filial_id == filial_id,
                Tempario.category == category,
                Tempario.sequence_number == sequence_number,
            )
        )
        if result.scalar_one_or_none() is not None:
            code = f"{CATEGORY_PREFIXES[category]}-{sequence_number}"
            raise TemparioCodeAlreadyExistsError(code)

    # Maintenance plans

    async def _get_plan_model(self, plan_id: uuid.UUID) -> MaintenancePlan:
        # populate_existing: within update_plan this re-reads the same
        # identity-mapped MaintenancePlan after replacing its entries in this
        # same session — without it, selectinload would leave the already
        # loaded (now-stale) `entries` collection untouched instead of
        # refreshing it from the rows just committed.
        query = (
            select(MaintenancePlan)
            .options(selectinload(MaintenancePlan.entries).selectinload(MaintenancePlanEntry.tempario))
            .where(MaintenancePlan.id == plan_id)
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(query)
        plan = result.scalar_one_or_none()
        if plan is None:
            raise MaintenancePlanNotFoundError(str(plan_id))
        return plan

    async def _ensure_temparios_belong(
        self, filial_id: uuid.UUID, tempario_ids: set[uuid.UUID]
    ) -> None:
        if not tempario_ids:
            return
        result = await self.db.execute(
            select(func.count())
            .select_from(Tempario)
            .where(Tempario.id.in_(tempario_ids), Tempario.filial_id == filial_id)
        )
        if result.scalar_one() != len(tempario_ids):
            raise BadRequestError("Uno o más servicios del plan no pertenecen a esta filial.")

    async def list_plans(
        self, filial_id: uuid.UUID, search: str | None = None
    ) -> list[MaintenancePlanRead]:
        query = (
            select(MaintenancePlan)
            .options(selectinload(MaintenancePlan.entries).selectinload(MaintenancePlanEntry.tempario))
            .where(MaintenancePlan.filial_id == filial_id)
            .order_by(MaintenancePlan.brand, MaintenancePlan.name)
        )
        result = await self.db.execute(query)
        plans = list(result.scalars().all())

        if search:
            term = search.lower()
            plans = [p for p in plans if term in p.brand.lower() or term in p.name.lower()]

        return [_plan_to_read(p) for p in plans]

    async def get_plan(self, plan_id: uuid.UUID) -> MaintenancePlanRead:
        plan = await self._get_plan_model(plan_id)
        return _plan_to_read(plan)

    async def create_plan(self, payload: MaintenancePlanCreate) -> MaintenancePlanRead:
        await self._ensure_temparios_belong(
            payload.filial_id, {e.tempario_id for e in payload.entries}
        )

        plan = MaintenancePlan(filial_id=payload.filial_id, brand=payload.brand, name=payload.name)
        self.db.add(plan)
        await self.db.flush()

        for entry in payload.entries:
            self.db.add(
                MaintenancePlanEntry(
                    plan_id=plan.id,
                    tempario_id=entry.tempario_id,
                    interval_km=entry.interval_km,
                    interval_months=entry.interval_months,
                )
            )

        await self.db.commit()
        return await self.get_plan(plan.id)

    async def update_plan(
        self, plan_id: uuid.UUID, payload: MaintenancePlanUpdate
    ) -> MaintenancePlanRead:
        plan = await self._get_plan_model(plan_id)

        if payload.entries is not None:
            await self._ensure_temparios_belong(
                plan.filial_id, {e.tempario_id for e in payload.entries}
            )

        if payload.brand is not None:
            plan.brand = payload.brand
        if payload.name is not None:
            plan.name = payload.name

        if payload.entries is not None:
            for existing in list(plan.entries):
                await self.db.delete(existing)
            await self.db.flush()
            for entry in payload.entries:
                self.db.add(
                    MaintenancePlanEntry(
                        plan_id=plan.id,
                        tempario_id=entry.tempario_id,
                        interval_km=entry.interval_km,
                        interval_months=entry.interval_months,
                    )
                )

        await self.db.commit()
        return await self.get_plan(plan_id)

    # Vehicle warranties (garantía de fábrica)

    def warranty_to_read(self, w: VehicleWarranty, current_mileage: int | None = None) -> VehicleWarrantyRead:
        status = "vencida" if w.expires_at is not None and w.expires_at < date.today() else "vigente"
        days_remaining = max((w.expires_at - date.today()).days, 0) if w.expires_at else None
        km_remaining = (
            max(w.duration_km - current_mileage, 0)
            if w.duration_km is not None and current_mileage is not None
            else None
        )
        return VehicleWarrantyRead(
            id=w.id,
            filial_id=w.filial_id,
            vin=w.vin,
            brand=w.brand,
            model=w.model,
            starts_at=w.starts_at,
            duration_months=w.duration_months,
            duration_km=w.duration_km,
            expires_at=w.expires_at,
            status=status,
            days_remaining=days_remaining,
            km_remaining=km_remaining,
            source=w.source,
            dealership_vehicle_id=w.dealership_vehicle_id,
            note=w.note,
            created_at=w.created_at,
        )

    @staticmethod
    def _expires_at(starts_at: date, duration_months: int | None) -> date | None:
        if duration_months is None:
            return None
        total = starts_at.month - 1 + duration_months
        year = starts_at.year + total // 12
        month = total % 12 + 1
        day = min(starts_at.day, calendar.monthrange(year, month)[1])
        return date(year, month, day)

    async def list_vehicle_warranties(
        self, filial_id: uuid.UUID, search: str | None = None
    ) -> list[VehicleWarrantyRead]:
        result = await self.db.execute(
            select(VehicleWarranty)
            .where(VehicleWarranty.filial_id == filial_id)
            .order_by(VehicleWarranty.created_at.desc())
        )
        warranties = list(result.scalars().all())
        if search:
            term = search.lower()
            warranties = [
                w
                for w in warranties
                if term in w.vin.lower() or term in w.brand.lower() or (w.model and term in w.model.lower())
            ]
        return [self.warranty_to_read(w) for w in warranties]

    async def get_vehicle_warranty_by_vin(self, filial_id: uuid.UUID, vin: str) -> VehicleWarrantyRead:
        result = await self.db.execute(
            select(VehicleWarranty).where(
                VehicleWarranty.filial_id == filial_id, VehicleWarranty.vin == vin.strip().upper()
            )
        )
        warranty = result.scalar_one_or_none()
        if warranty is None:
            raise VehicleWarrantyNotFoundError(vin)
        return self.warranty_to_read(warranty)

    async def list_vigente_warranties_by_vin(
        self, filial_id: uuid.UUID, vin: str, current_mileage: int | None
    ) -> list[VehicleWarrantyRead]:
        result = await self.db.execute(
            select(VehicleWarranty).where(
                VehicleWarranty.filial_id == filial_id, VehicleWarranty.vin == vin.strip().upper()
            )
        )
        reads = [self.warranty_to_read(w, current_mileage=current_mileage) for w in result.scalars()]
        return [r for r in reads if r.status == "vigente"]

    async def create_vehicle_warranty(
        self, payload: VehicleWarrantyCreate, created_by_user_id: uuid.UUID | None
    ) -> VehicleWarrantyRead:
        existing = await self.db.execute(
            select(VehicleWarranty).where(
                VehicleWarranty.filial_id == payload.filial_id, VehicleWarranty.vin == payload.vin
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise VehicleWarrantyAlreadyExistsError(payload.vin)

        warranty = VehicleWarranty(
            filial_id=payload.filial_id,
            vin=payload.vin,
            brand=payload.brand,
            model=payload.model,
            starts_at=payload.starts_at,
            duration_months=payload.duration_months,
            duration_km=payload.duration_km,
            expires_at=self._expires_at(payload.starts_at, payload.duration_months),
            source=VehicleWarrantySource.MANUAL,
            note=payload.note,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(warranty)
        await self.db.commit()
        await self.db.refresh(warranty)
        return self.warranty_to_read(warranty)

    async def bulk_create_vehicle_warranties(
        self, filial_id: uuid.UUID, items: list[VehicleWarrantyBulkItem], created_by_user_id: uuid.UUID | None
    ) -> tuple[list[VehicleWarranty], list[str]]:
        """Mirrors PartsService.bulk_create_parts: duplicates (against the
        database or repeated within the same batch) are skipped, not
        rejected — bulk imports commonly re-upload the same file more than
        once."""
        existing_result = await self.db.execute(
            select(VehicleWarranty.vin).where(VehicleWarranty.filial_id == filial_id)
        )
        existing_vins = {row[0] for row in existing_result.all()}

        created: list[VehicleWarranty] = []
        skipped: list[str] = []
        seen_in_batch: set[str] = set()

        for item in items:
            if item.vin in existing_vins or item.vin in seen_in_batch:
                skipped.append(item.vin)
                continue
            warranty = VehicleWarranty(
                filial_id=filial_id,
                vin=item.vin,
                brand=item.brand,
                model=item.model,
                starts_at=item.starts_at,
                duration_months=item.duration_months,
                duration_km=item.duration_km,
                expires_at=self._expires_at(item.starts_at, item.duration_months),
                source=VehicleWarrantySource.MANUAL,
                note=item.note,
                created_by_user_id=created_by_user_id,
            )
            self.db.add(warranty)
            created.append(warranty)
            seen_in_batch.add(item.vin)

        await self.db.commit()
        for warranty in created:
            await self.db.refresh(warranty)
        return created, skipped

    async def create_or_renew_warranty_from_sale(
        self,
        filial_id: uuid.UUID,
        vin: str,
        brand: str,
        model: str | None,
        sale_date: date,
        dealership_vehicle_id: uuid.UUID,
    ) -> VehicleWarranty:
        """Called from ConcesionarioService when a DealershipVehicle sale
        closes. A fresh sale means the factory-warranty clock restarts from
        today, whether or not this VIN already had one on file (e.g. a
        used-car resale) — so this upserts rather than skipping or erroring."""
        vin = vin.strip().upper()
        settings = await self.get_labor_settings(filial_id)
        default_months = settings.vehicle_warranty_default_months

        result = await self.db.execute(
            select(VehicleWarranty).where(VehicleWarranty.filial_id == filial_id, VehicleWarranty.vin == vin)
        )
        warranty = result.scalar_one_or_none()
        if warranty is None:
            warranty = VehicleWarranty(filial_id=filial_id, vin=vin)
            self.db.add(warranty)

        warranty.brand = brand
        warranty.model = model
        warranty.starts_at = sale_date
        warranty.duration_months = default_months
        warranty.duration_km = None
        warranty.expires_at = self._expires_at(sale_date, default_months)
        warranty.source = VehicleWarrantySource.VENTA
        warranty.dealership_vehicle_id = dealership_vehicle_id
        warranty.note = None

        await self.db.commit()
        await self.db.refresh(warranty)
        return warranty

    def workshop_warranty_to_read(self, w: WorkshopWarranty) -> WorkshopWarrantyRead:
        status = "vencida" if w.expires_at < date.today() else "vigente"
        days_remaining = max((w.expires_at - date.today()).days, 0)
        return WorkshopWarrantyRead(
            id=w.id,
            filial_id=w.filial_id,
            vin=w.vin,
            service_order_id=w.service_order_id,
            service_order_task_id=w.service_order_task_id,
            coverage_type=w.coverage_type,
            tempario_code_snapshot=w.tempario_code_snapshot,
            tempario_name_snapshot=w.tempario_name_snapshot,
            technician_user_id=w.technician_user_id,
            starts_at=w.starts_at,
            duration_days=w.duration_days,
            duration_km=w.duration_km,
            expires_at=w.expires_at,
            expiration_mileage=w.expiration_mileage,
            status=status,
            days_remaining=days_remaining,
            created_at=w.created_at,
        )

    async def list_workshop_warranties_by_vin(self, filial_id: uuid.UUID, vin: str) -> list[WorkshopWarrantyRead]:
        result = await self.db.execute(
            select(WorkshopWarranty)
            .where(WorkshopWarranty.filial_id == filial_id, WorkshopWarranty.vin == vin.strip().upper())
            .order_by(WorkshopWarranty.created_at.desc())
        )
        return [self.workshop_warranty_to_read(w) for w in result.scalars()]