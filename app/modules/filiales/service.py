import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.filiales.exceptions import FilialNotFoundError, FilialSlugAlreadyExistsError
from app.modules.filiales.models import Filial
from app.modules.filiales.schemas import FilialCreate, FilialUpdate
from app.modules.holdings.service import HoldingService


class FilialService:
    """Business logic for creating and managing filiales within a holding."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.holding_service = HoldingService(db)

    async def list_filiales(self, holding_id: uuid.UUID | None = None) -> list[Filial]:
        query = select(Filial).order_by(Filial.created_at.desc())
        if holding_id is not None:
            query = query.where(Filial.holding_id == holding_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_filial(self, filial_id: uuid.UUID) -> Filial:
        filial = await self.db.get(Filial, filial_id)
        if filial is None:
            raise FilialNotFoundError(str(filial_id))
        return filial

    async def create_filial(self, payload: FilialCreate) -> Filial:
        # Ensures the holding exists before creating a filial under it.
        await self.holding_service.get_holding(payload.holding_id)
        await self._ensure_slug_is_available(payload.holding_id, payload.slug)

        filial = Filial(holding_id=payload.holding_id, name=payload.name, slug=payload.slug)
        self.db.add(filial)
        await self.db.commit()
        await self.db.refresh(filial)
        return filial

    async def update_filial(self, filial_id: uuid.UUID, payload: FilialUpdate) -> Filial:
        filial = await self.get_filial(filial_id)

        if payload.slug and payload.slug != filial.slug:
            await self._ensure_slug_is_available(filial.holding_id, payload.slug)
            filial.slug = payload.slug

        if payload.name:
            filial.name = payload.name

        await self.db.commit()
        await self.db.refresh(filial)
        return filial

    async def set_active_status(self, filial_id: uuid.UUID, is_active: bool) -> Filial:
        filial = await self.get_filial(filial_id)
        filial.is_active = is_active
        await self.db.commit()
        await self.db.refresh(filial)
        return filial

    async def _ensure_slug_is_available(self, holding_id: uuid.UUID, slug: str) -> None:
        result = await self.db.execute(
            select(Filial).where(Filial.holding_id == holding_id, Filial.slug == slug)
        )
        if result.scalar_one_or_none() is not None:
            raise FilialSlugAlreadyExistsError(slug)
