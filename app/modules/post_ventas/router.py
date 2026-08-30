import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.post_ventas.schemas import (
    LaborSettingsRead,
    LaborSettingsUpdate,
    TemparioCreate,
    TemparioRead,
    TemparioUpdate,
)
from app.modules.post_ventas.service import PostVentasService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "post-ventas"

router = APIRouter(tags=["Post Ventas"])


def get_service(db: AsyncSession = Depends(get_db)) -> PostVentasService:
    return PostVentasService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("/labor-settings", response_model=LaborSettingsRead)
async def get_labor_settings(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> LaborSettingsRead:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.get_labor_settings(filial_id)


@router.patch("/labor-settings", response_model=LaborSettingsRead)
async def update_labor_settings(
    payload: LaborSettingsUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> LaborSettingsRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_labor_settings(filial_id, payload)


@router.get("/temparios", response_model=list[TemparioRead])
async def list_temparios(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> list[TemparioRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_temparios(filial_id, search)


@router.get("/temparios/{tempario_id}", response_model=TemparioRead)
async def get_tempario(
    tempario_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> TemparioRead:
    tempario = await service.get_tempario(tempario_id)
    await _ensure_access(current_user, tempario.filial_id, service.db)
    return tempario


@router.post("/temparios", response_model=TemparioRead, status_code=status.HTTP_201_CREATED)
async def create_tempario(
    payload: TemparioCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> TemparioRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_tempario(payload)


@router.patch("/temparios/{tempario_id}", response_model=TemparioRead)
async def update_tempario(
    tempario_id: uuid.UUID,
    payload: TemparioUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PostVentasService = Depends(get_service),
) -> TemparioRead:
    existing = await service.get_tempario(tempario_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_tempario(tempario_id, payload)