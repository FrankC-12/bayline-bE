import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.inspections.schemas import InspectionCreate, InspectionRead, InspectionUpdate
from app.modules.inspections.service import InspectionService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "asesor-servicios"

router = APIRouter(prefix="/inspections", tags=["Inspections"])


def get_service(db: AsyncSession = Depends(get_db)) -> InspectionService:
    return InspectionService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("", response_model=list[InspectionRead])
async def list_inspections(
    filial_id: uuid.UUID = Query(...),
    unlinked_only: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: InspectionService = Depends(get_service),
) -> list[InspectionRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_inspections(filial_id, unlinked_only)


@router.get("/by-order/{service_order_id}", response_model=InspectionRead | None)
async def get_inspection_for_order(
    service_order_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: InspectionService = Depends(get_service),
) -> InspectionRead | None:
    inspection = await service.get_for_order(service_order_id)
    if inspection is not None:
        await _ensure_access(current_user, inspection.filial_id, service.db)
    return inspection


@router.post("", response_model=InspectionRead, status_code=status.HTTP_201_CREATED)
async def create_inspection(
    payload: InspectionCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: InspectionService = Depends(get_service),
) -> InspectionRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_inspection(payload, current_user.user_id)


@router.patch("/{inspection_id}", response_model=InspectionRead)
async def update_inspection(
    inspection_id: uuid.UUID,
    payload: InspectionUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: InspectionService = Depends(get_service),
) -> InspectionRead:
    existing = await service.get_inspection(inspection_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_inspection(inspection_id, payload)


@router.delete("/{inspection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_inspection(
    inspection_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: InspectionService = Depends(get_service),
) -> None:
    existing = await service.get_inspection(inspection_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    await service.delete_inspection(inspection_id)