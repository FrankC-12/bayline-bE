import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.holdings.exceptions import HoldingNotFoundError, HoldingSlugAlreadyExistsError
from app.modules.holdings.models import Holding
from app.modules.holdings.schemas import HoldingCreate, HoldingUpdate


class HoldingService:
    """Business logic for creating and managing holdings. Platform-only by design."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_holdings(self) -> list[Holding]:
        result = await self.db.execute(select(Holding).order_by(Holding.created_at.desc()))
        return list(result.scalars().all())

    async def get_holding(self, holding_id: uuid.UUID) -> Holding:
        holding = await self.db.get(Holding, holding_id)
        if holding is None:
            raise HoldingNotFoundError(str(holding_id))
        return holding

    async def create_holding(self, payload: HoldingCreate) -> Holding:
        await self._ensure_slug_is_available(payload.slug)
        holding = Holding(name=payload.name, slug=payload.slug)
        self.db.add(holding)
        await self.db.commit()
        await self.db.refresh(holding)
        return holding

    async def update_holding(self, holding_id: uuid.UUID, payload: HoldingUpdate) -> Holding:
        holding = await self.get_holding(holding_id)

        if payload.slug and payload.slug != holding.slug:
            await self._ensure_slug_is_available(payload.slug)
            holding.slug = payload.slug

        if payload.name:
            holding.name = payload.name

        await self.db.commit()
        await self.db.refresh(holding)
        return holding

    async def set_active_status(self, holding_id: uuid.UUID, is_active: bool) -> Holding:
        holding = await self.get_holding(holding_id)
        holding.is_active = is_active
        await self.db.commit()
        await self.db.refresh(holding)
        return holding

    async def _ensure_slug_is_available(self, slug: str) -> None:
        result = await self.db.execute(select(Holding).where(Holding.slug == slug))
        if result.scalar_one_or_none() is not None:
            raise HoldingSlugAlreadyExistsError(slug)
