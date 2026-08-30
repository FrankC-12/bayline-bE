import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.post_ventas.enums import CATEGORY_PREFIXES, TemparioCategory
from app.modules.post_ventas.exceptions import TemparioCodeAlreadyExistsError, TemparioNotFoundError
from app.modules.post_ventas.models import LaborSettings, Tempario, TemparioPart
from app.modules.post_ventas.schemas import (
    CompatibleVehicle,
    LaborSettingsUpdate,
    TemparioCreate,
    TemparioPartRead,
    TemparioRead,
    TemparioUpdate,
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


class PostVentasService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # Labor settings

    async def get_labor_settings(self, filial_id: uuid.UUID) -> LaborSettings:
        result = await self.db.execute(select(LaborSettings).where(LaborSettings.filial_id == filial_id))
        settings = result.scalar_one_or_none()
        if settings is None:
            settings = LaborSettings(filial_id=filial_id)
            self.db.add(settings)
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
        settings.bcv_rate = payload.bcv_rate
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