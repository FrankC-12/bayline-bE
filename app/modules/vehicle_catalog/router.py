import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.exceptions import FilialNotFoundError
from app.modules.filiales.models import Filial
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access
from app.modules.vehicle_catalog.schemas import (
    VehicleBrandCreate,
    VehicleBrandRead,
    VehicleBrandUpdate,
    VehicleModelCreate,
    VehicleModelRead,
    VehicleModelUpdate,
)
from app.modules.vehicle_catalog.service import VehicleCatalogService

MODULE_ID = "ajustes"

router = APIRouter(prefix="/vehicle-catalog", tags=["Vehicle Catalog"])


def get_service(db: AsyncSession = Depends(get_db)) -> VehicleCatalogService:
    return VehicleCatalogService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


async def _holding_id_for_filial(db: AsyncSession, filial_id: uuid.UUID) -> uuid.UUID:
    filial = await db.get(Filial, filial_id)
    if filial is None:
        raise FilialNotFoundError(str(filial_id))
    return filial.holding_id


@router.get("/brands", response_model=list[VehicleBrandRead])
async def list_brands(
    filial_id: uuid.UUID = Query(...),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> list[VehicleBrandRead]:
    """The holding-wide brand/model catalog — shared by every filial. Used both
    by the Ajustes management screen (include_inactive=true) and by every
    brand/model select elsewhere in the app (active only, the default)."""
    await _ensure_access(current_user, filial_id, db)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.list_brands(holding_id, include_inactive)


@router.post("/brands", response_model=VehicleBrandRead, status_code=status.HTTP_201_CREATED)
async def create_brand(
    payload: VehicleBrandCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleBrandRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.create_brand(holding_id, payload)


@router.patch("/brands/{brand_id}", response_model=VehicleBrandRead)
async def update_brand(
    brand_id: uuid.UUID,
    payload: VehicleBrandUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleBrandRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.update_brand(brand_id, holding_id, payload)


@router.post("/brands/{brand_id}/activate", response_model=VehicleBrandRead)
async def activate_brand(
    brand_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleBrandRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.set_brand_active(brand_id, holding_id, is_active=True)


@router.post("/brands/{brand_id}/deactivate", response_model=VehicleBrandRead)
async def deactivate_brand(
    brand_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleBrandRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.set_brand_active(brand_id, holding_id, is_active=False)


@router.post(
    "/brands/{brand_id}/models", response_model=VehicleModelRead, status_code=status.HTTP_201_CREATED
)
async def create_model(
    brand_id: uuid.UUID,
    payload: VehicleModelCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleModelRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.create_model(brand_id, holding_id, payload)


@router.patch("/models/{model_id}", response_model=VehicleModelRead)
async def update_model(
    model_id: uuid.UUID,
    payload: VehicleModelUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleModelRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.update_model(model_id, holding_id, payload)


@router.post("/models/{model_id}/activate", response_model=VehicleModelRead)
async def activate_model(
    model_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleModelRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.set_model_active(model_id, holding_id, is_active=True)


@router.post("/models/{model_id}/deactivate", response_model=VehicleModelRead)
async def deactivate_model(
    model_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: VehicleCatalogService = Depends(get_service),
) -> VehicleModelRead:
    await _ensure_access(current_user, filial_id, db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(db, filial_id)
    return await service.set_model_active(model_id, holding_id, is_active=False)
