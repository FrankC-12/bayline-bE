import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_platform_user
from app.modules.auth.schemas import CurrentUser
from app.modules.holdings.schemas import HoldingCreate, HoldingRead, HoldingUpdate
from app.modules.holdings.service import HoldingService

router = APIRouter(prefix="/holdings", tags=["Holdings"])


def get_holding_service(db: AsyncSession = Depends(get_db)) -> HoldingService:
    return HoldingService(db)


@router.get("", response_model=list[HoldingRead])
async def list_holdings(
    current_user: CurrentUser = Depends(get_current_user),
    service: HoldingService = Depends(get_holding_service),
) -> list[HoldingRead]:
    """List holdings. Platform sees every holding; Holding/Filial callers see only their own."""
    holdings = await service.list_holdings()
    if current_user.holding_id is not None:
        holdings = [h for h in holdings if h.id == current_user.holding_id]
    return holdings


@router.get("/{holding_id}", response_model=HoldingRead)
async def get_holding(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Retrieve a single holding by its id."""
    return await service.get_holding(holding_id)


@router.post("", response_model=HoldingRead, status_code=status.HTTP_201_CREATED)
async def create_holding(
    payload: HoldingCreate,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Create a new holding. Platform-only."""
    return await service.create_holding(payload)


@router.patch("/{holding_id}", response_model=HoldingRead)
async def update_holding(
    holding_id: uuid.UUID,
    payload: HoldingUpdate,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Update a holding's name or slug. Platform-only."""
    return await service.update_holding(holding_id, payload)


@router.post("/{holding_id}/activate", response_model=HoldingRead)
async def activate_holding(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Reactivate a previously deactivated holding. Platform-only."""
    return await service.set_active_status(holding_id, is_active=True)


@router.post("/{holding_id}/deactivate", response_model=HoldingRead)
async def deactivate_holding(
    holding_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_platform_user),
    service: HoldingService = Depends(get_holding_service),
) -> HoldingRead:
    """Deactivate a holding without deleting its historical data. Platform-only."""
    return await service.set_active_status(holding_id, is_active=False)
