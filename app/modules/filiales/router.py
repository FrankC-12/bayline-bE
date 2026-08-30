import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_holding_user
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.schemas import FilialCreate, FilialRead, FilialUpdate
from app.modules.filiales.service import FilialService

router = APIRouter(prefix="/filiales", tags=["Filiales"])


def get_filial_service(db: AsyncSession = Depends(get_db)) -> FilialService:
    return FilialService(db)


def _ensure_owns_holding(current_user: CurrentUser, holding_id: uuid.UUID) -> None:
    if current_user.holding_id != holding_id:
        raise InsufficientPermissionsError()


@router.get("", response_model=list[FilialRead])
async def list_filiales(
    holding_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: FilialService = Depends(get_filial_service),
) -> list[FilialRead]:
    """List filiales. Holding/Filial callers are always scoped to their own holding."""
    if current_user.holding_id is not None:
        holding_id = current_user.holding_id
    return await service.list_filiales(holding_id)


@router.get("/{filial_id}", response_model=FilialRead)
async def get_filial(
    filial_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: FilialService = Depends(get_filial_service),
) -> FilialRead:
    """Retrieve a single filial by its id."""
    return await service.get_filial(filial_id)


@router.post("", response_model=FilialRead, status_code=status.HTTP_201_CREATED)
async def create_filial(
    payload: FilialCreate,
    current_user: CurrentUser = Depends(require_holding_user),
    service: FilialService = Depends(get_filial_service),
) -> FilialRead:
    """Create a new filial. Only the owning Holding may create filiales under itself."""
    _ensure_owns_holding(current_user, payload.holding_id)
    return await service.create_filial(payload)


@router.patch("/{filial_id}", response_model=FilialRead)
async def update_filial(
    filial_id: uuid.UUID,
    payload: FilialUpdate,
    current_user: CurrentUser = Depends(require_holding_user),
    service: FilialService = Depends(get_filial_service),
) -> FilialRead:
    """Update a filial. Only its owning Holding may modify it."""
    existing = await service.get_filial(filial_id)
    _ensure_owns_holding(current_user, existing.holding_id)
    return await service.update_filial(filial_id, payload)


@router.post("/{filial_id}/activate", response_model=FilialRead)
async def activate_filial(
    filial_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_holding_user),
    service: FilialService = Depends(get_filial_service),
) -> FilialRead:
    """Reactivate a filial. Only its owning Holding may do this."""
    existing = await service.get_filial(filial_id)
    _ensure_owns_holding(current_user, existing.holding_id)
    return await service.set_active_status(filial_id, is_active=True)


@router.post("/{filial_id}/deactivate", response_model=FilialRead)
async def deactivate_filial(
    filial_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_holding_user),
    service: FilialService = Depends(get_filial_service),
) -> FilialRead:
    """Deactivate a filial. Only its owning Holding may do this."""
    existing = await service.get_filial(filial_id)
    _ensure_owns_holding(current_user, existing.holding_id)
    return await service.set_active_status(filial_id, is_active=False)
