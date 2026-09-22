import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.vehicle_catalog.exceptions import (
    VehicleBrandNameAlreadyExistsError,
    VehicleBrandNotFoundError,
    VehicleModelNameAlreadyExistsError,
    VehicleModelNotFoundError,
)
from app.modules.vehicle_catalog.models import VehicleBrand, VehicleModel
from app.modules.vehicle_catalog.schemas import (
    VehicleBrandCreate,
    VehicleBrandUpdate,
    VehicleModelCreate,
    VehicleModelUpdate,
)

# Preloaded for every holding — new ones at creation time (see
# seed_default_brands, called from HoldingService.create_holding), existing
# ones backfilled by the migration that introduced this catalog. Models are
# deliberately left empty: an admin adds them from Ajustes as needed.
DEFAULT_BRANDS = [
    "Toyota",
    "Chevrolet",
    "Ford",
    "JAC",
    "Chery",
    "Hyundai",
    "Kia",
    "Mitsubishi",
    "Dongfeng",
    "Great Wall",
    "Mazda",
    "Jeep",
]


class VehicleCatalogService:
    """Business logic for the holding-wide vehicle brand/model catalog that
    feeds every brand/model select across the app."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def seed_default_brands(self, holding_id: uuid.UUID) -> None:
        for name in DEFAULT_BRANDS:
            self.db.add(VehicleBrand(holding_id=holding_id, name=name))
        await self.db.commit()

    # Brands

    async def list_brands(self, holding_id: uuid.UUID, include_inactive: bool = False) -> list[VehicleBrand]:
        query = (
            select(VehicleBrand)
            .options(selectinload(VehicleBrand.models))
            .where(VehicleBrand.holding_id == holding_id)
            .order_by(VehicleBrand.name)
            .execution_options(populate_existing=True)
        )
        if not include_inactive:
            query = query.where(VehicleBrand.is_active.is_(True))
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_brand(self, brand_id: uuid.UUID, holding_id: uuid.UUID) -> VehicleBrand:
        # populate_existing — a brand already in the identity map (e.g. just
        # created, before any of its models existed) must not serve a stale,
        # empty `models` collection after one is added.
        result = await self.db.execute(
            select(VehicleBrand)
            .options(selectinload(VehicleBrand.models))
            .where(VehicleBrand.id == brand_id, VehicleBrand.holding_id == holding_id)
            .execution_options(populate_existing=True)
        )
        brand = result.scalar_one_or_none()
        if brand is None:
            raise VehicleBrandNotFoundError(str(brand_id))
        return brand

    async def create_brand(self, holding_id: uuid.UUID, payload: VehicleBrandCreate) -> VehicleBrand:
        await self._ensure_brand_name_is_available(holding_id, payload.name)
        brand = VehicleBrand(holding_id=holding_id, name=payload.name.strip())
        self.db.add(brand)
        await self.db.commit()
        await self.db.refresh(brand)
        return await self.get_brand(brand.id, holding_id)

    async def update_brand(
        self, brand_id: uuid.UUID, holding_id: uuid.UUID, payload: VehicleBrandUpdate
    ) -> VehicleBrand:
        brand = await self.get_brand(brand_id, holding_id)
        if payload.name and payload.name.strip() != brand.name:
            await self._ensure_brand_name_is_available(holding_id, payload.name)
            brand.name = payload.name.strip()
        await self.db.commit()
        return await self.get_brand(brand_id, holding_id)

    async def set_brand_active(
        self, brand_id: uuid.UUID, holding_id: uuid.UUID, is_active: bool
    ) -> VehicleBrand:
        brand = await self.get_brand(brand_id, holding_id)
        brand.is_active = is_active
        await self.db.commit()
        return await self.get_brand(brand_id, holding_id)

    async def _ensure_brand_name_is_available(self, holding_id: uuid.UUID, name: str) -> None:
        result = await self.db.execute(
            select(VehicleBrand).where(
                VehicleBrand.holding_id == holding_id,
                func.lower(VehicleBrand.name) == name.strip().lower(),
            )
        )
        if result.scalar_one_or_none() is not None:
            raise VehicleBrandNameAlreadyExistsError(name)

    # Models

    async def get_model(self, model_id: uuid.UUID, holding_id: uuid.UUID) -> VehicleModel:
        result = await self.db.execute(
            select(VehicleModel)
            .join(VehicleBrand, VehicleBrand.id == VehicleModel.brand_id)
            .where(VehicleModel.id == model_id, VehicleBrand.holding_id == holding_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise VehicleModelNotFoundError(str(model_id))
        return model

    async def create_model(
        self, brand_id: uuid.UUID, holding_id: uuid.UUID, payload: VehicleModelCreate
    ) -> VehicleModel:
        await self.get_brand(brand_id, holding_id)  # 404s if it's not this holding's brand
        await self._ensure_model_name_is_available(brand_id, payload.name)
        model = VehicleModel(brand_id=brand_id, name=payload.name.strip(), vehicle_type=payload.vehicle_type)
        self.db.add(model)
        await self.db.commit()
        await self.db.refresh(model)
        return model

    async def update_model(
        self, model_id: uuid.UUID, holding_id: uuid.UUID, payload: VehicleModelUpdate
    ) -> VehicleModel:
        model = await self.get_model(model_id, holding_id)
        if payload.name and payload.name.strip() != model.name:
            await self._ensure_model_name_is_available(model.brand_id, payload.name)
            model.name = payload.name.strip()
        if payload.vehicle_type is not None:
            model.vehicle_type = payload.vehicle_type
        await self.db.commit()
        await self.db.refresh(model)
        return model

    async def set_model_active(
        self, model_id: uuid.UUID, holding_id: uuid.UUID, is_active: bool
    ) -> VehicleModel:
        model = await self.get_model(model_id, holding_id)
        model.is_active = is_active
        await self.db.commit()
        await self.db.refresh(model)
        return model

    async def _ensure_model_name_is_available(self, brand_id: uuid.UUID, name: str) -> None:
        result = await self.db.execute(
            select(VehicleModel).where(
                VehicleModel.brand_id == brand_id,
                func.lower(VehicleModel.name) == name.strip().lower(),
            )
        )
        if result.scalar_one_or_none() is not None:
            raise VehicleModelNameAlreadyExistsError(name)
