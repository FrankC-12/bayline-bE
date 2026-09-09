"""Business-wide settings (IVA, IGTF, tasa BCV, comisión, mano de obra).

These used to live under the post_ventas module, gated by the same
permission as the Temparios catalog. They're business-wide financial/
operational parameters, not part of the service catalog, so they get their
own module id — the persistence (LaborSettings) and business logic stay in
post_ventas.service, only the access-control boundary moves here.
"""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.post_ventas.schemas import LaborSettingsRead, LaborSettingsUpdate
from app.modules.post_ventas.service import PostVentasService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "ajustes"

router = APIRouter(tags=["Ajustes"])


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
