import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.parts.schemas import (
    PartBulkCreate,
    PartBulkResult,
    PartCreate,
    PartRead,
    PartReturnCreate,
    PartReturnRead,
    PartSaleCreate,
    PartSaleRead,
    PartSaleUpdate,
    PartUpdate,
)
from app.modules.parts.service import PartsService
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access

MODULE_ID = "repuestos"

router = APIRouter(tags=["Parts"])


def get_service(db: AsyncSession = Depends(get_db)) -> PartsService:
    return PartsService(db)


async def _ensure_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    await ensure_module_access(db, current_user, filial_id, MODULE_ID, level)


@router.get("/parts", response_model=list[PartRead])
async def list_parts(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_parts(filial_id, search)


@router.post("/parts", response_model=PartRead, status_code=status.HTTP_201_CREATED)
async def create_part(
    payload: PartCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_part(payload)


@router.post("/parts/bulk", response_model=PartBulkResult, status_code=status.HTTP_201_CREATED)
async def bulk_create_parts(
    payload: PartBulkCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartBulkResult:
    """Create many parts at once (from a pasted list or an uploaded spreadsheet).
    Items whose code already exists are skipped, not rejected."""
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    created, skipped = await service.bulk_create_parts(payload.filial_id, payload.items)
    return PartBulkResult(created=created, skipped=skipped)


@router.patch("/parts/{part_id}", response_model=PartRead)
async def update_part(
    part_id: uuid.UUID,
    payload: PartUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    existing = await service.get_part(part_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.update_part(part_id, payload)


@router.get("/part-sales", response_model=list[PartSaleRead])
async def list_part_sales(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartSaleRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_sales(filial_id, search)


@router.get("/part-sales/{sale_id}", response_model=PartSaleRead)
async def get_part_sale(
    sale_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSaleRead:
    sale = await service.get_sale(sale_id)
    await _ensure_access(current_user, sale.filial_id, service.db)
    return sale


@router.post("/part-sales", response_model=PartSaleRead, status_code=status.HTTP_201_CREATED)
async def create_part_sale(
    payload: PartSaleCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSaleRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_sale(payload)


@router.patch("/part-sales/{sale_id}", response_model=PartSaleRead)
async def update_part_sale(
    sale_id: uuid.UUID,
    payload: PartSaleUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartSaleRead:
    existing = await service.get_sale(sale_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    if payload.status is None:
        return existing
    return await service.update_sale_status(sale_id, payload.status)


@router.get("/part-returns", response_model=list[PartReturnRead])
async def list_part_returns(
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartReturnRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_returns(filial_id)


@router.post("/part-returns", response_model=PartReturnRead, status_code=status.HTTP_201_CREATED)
async def create_part_return(
    payload: PartReturnCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartReturnRead:
    await _ensure_access(current_user, payload.filial_id, service.db, AccessLevel.EDITAR)
    return await service.create_return(payload, current_user.user_id)