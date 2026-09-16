import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.storage import save_upload_image
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.exceptions import FilialNotFoundError
from app.modules.filiales.models import Filial
from app.modules.parts.enums import ReturnCondition, ReturnReason
from app.modules.parts.exceptions import MissingReturnPhotoError
from app.modules.parts.schemas import (
    PartBulkCreate,
    PartBulkResult,
    PartCategoryCreate,
    PartCategoryRead,
    PartCategoryUpdate,
    PartCreate,
    PartMeasureCreate,
    PartMeasureRead,
    PartMeasureUpdate,
    PartRead,
    PartReturnCreate,
    PartReturnRead,
    PartSaleCreate,
    PartSaleQuoteInput,
    PartSaleQuoteRead,
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


async def _holding_id_for_filial(db: AsyncSession, filial_id: uuid.UUID) -> uuid.UUID:
    filial = await db.get(Filial, filial_id)
    if filial is None:
        raise FilialNotFoundError(str(filial_id))
    return filial.holding_id


@router.get("/parts", response_model=list[PartRead])
async def list_parts(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_parts(filial_id, search, include_inactive)


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


@router.post("/parts/{part_id}/activate", response_model=PartRead)
async def activate_part(
    part_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    existing = await service.get_part(part_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.set_part_active(part_id, is_active=True)


@router.post("/parts/{part_id}/deactivate", response_model=PartRead)
async def deactivate_part(
    part_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartRead:
    existing = await service.get_part(part_id)
    await _ensure_access(current_user, existing.filial_id, service.db, AccessLevel.EDITAR)
    return await service.set_part_active(part_id, is_active=False)


# Part categories (Ajustes → Categorías de Repuestos) — holding-wide, same
# access boundary as the parts catalog itself.


@router.get("/part-categories", response_model=list[PartCategoryRead])
async def list_part_categories(
    filial_id: uuid.UUID = Query(...),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartCategoryRead]:
    await _ensure_access(current_user, filial_id, service.db)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.list_categories(holding_id, include_inactive)


@router.post("/part-categories", response_model=PartCategoryRead, status_code=status.HTTP_201_CREATED)
async def create_part_category(
    payload: PartCategoryCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.create_category(holding_id, payload)


@router.patch("/part-categories/{category_id}", response_model=PartCategoryRead)
async def update_part_category(
    category_id: uuid.UUID,
    payload: PartCategoryUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.update_category(category_id, holding_id, payload)


@router.post("/part-categories/{category_id}/activate", response_model=PartCategoryRead)
async def activate_part_category(
    category_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_category_active(category_id, holding_id, is_active=True)


@router.post("/part-categories/{category_id}/deactivate", response_model=PartCategoryRead)
async def deactivate_part_category(
    category_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartCategoryRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_category_active(category_id, holding_id, is_active=False)


# Part measures (Ajustes → Medidas de Repuestos) — holding-wide.


@router.get("/part-measures", response_model=list[PartMeasureRead])
async def list_part_measures(
    filial_id: uuid.UUID = Query(...),
    include_inactive: bool = Query(default=False),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartMeasureRead]:
    await _ensure_access(current_user, filial_id, service.db)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.list_measures(holding_id, include_inactive)


@router.post("/part-measures", response_model=PartMeasureRead, status_code=status.HTTP_201_CREATED)
async def create_part_measure(
    payload: PartMeasureCreate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.create_measure(holding_id, payload)


@router.patch("/part-measures/{measure_id}", response_model=PartMeasureRead)
async def update_part_measure(
    measure_id: uuid.UUID,
    payload: PartMeasureUpdate,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.update_measure(measure_id, holding_id, payload)


@router.post("/part-measures/{measure_id}/activate", response_model=PartMeasureRead)
async def activate_part_measure(
    measure_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_measure_active(measure_id, holding_id, is_active=True)


@router.post("/part-measures/{measure_id}/deactivate", response_model=PartMeasureRead)
async def deactivate_part_measure(
    measure_id: uuid.UUID,
    filial_id: uuid.UUID = Query(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartMeasureRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    holding_id = await _holding_id_for_filial(service.db, filial_id)
    return await service.set_measure_active(measure_id, holding_id, is_active=False)


@router.get("/part-sales", response_model=list[PartSaleRead])
async def list_part_sales(
    filial_id: uuid.UUID = Query(...),
    search: str | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> list[PartSaleRead]:
    await _ensure_access(current_user, filial_id, service.db)
    return await service.list_sales(filial_id, search)


@router.post("/part-sales/quote", response_model=PartSaleQuoteRead)
async def quote_part_sale(
    payload: PartSaleQuoteInput,
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
):
    await _ensure_access(current_user, payload.filial_id, service.db)
    return await service.quote_sale(payload)


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
    return await service.update_sale_status(sale_id, payload.status, payload.dispatched_lines)


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
    filial_id: uuid.UUID = Form(...),
    part_id: uuid.UUID = Form(...),
    condition: ReturnCondition = Form(...),
    origin_warehouse: str = Form(...),
    destination_warehouse: str = Form(...),
    quantity: int = Form(...),
    reason: ReturnReason = Form(...),
    reason_notes: str | None = Form(None),
    photos: list[UploadFile] = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    service: PartsService = Depends(get_service),
) -> PartReturnRead:
    await _ensure_access(current_user, filial_id, service.db, AccessLevel.EDITAR)
    if not photos:
        raise MissingReturnPhotoError()

    settings = get_settings()
    photo_urls = [
        await save_upload_image(
            photo,
            directory=Path(settings.uploads_dir),
            subdir="part-returns",
            url_prefix=f"{settings.api_v1_prefix}/uploads",
            max_mb=settings.max_upload_mb,
        )
        for photo in photos
    ]

    payload = PartReturnCreate(
        filial_id=filial_id,
        part_id=part_id,
        condition=condition,
        origin_warehouse=origin_warehouse,
        destination_warehouse=destination_warehouse,
        quantity=quantity,
        reason=reason,
        reason_notes=reason_notes,
        photo_urls=photo_urls,
    )
    return await service.create_return(payload, current_user.user_id)
