import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_platform_user
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import RoleScope
from app.modules.roles.schemas import RoleCreate, RoleRead, RoleUpdate
from app.modules.roles.service import RoleService

router = APIRouter(prefix="/roles", tags=["Roles"])


def get_role_service(db: AsyncSession = Depends(get_db)) -> RoleService:
    return RoleService(db)


@router.get("", response_model=list[RoleRead])
async def list_roles(
    scope: RoleScope | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: RoleService = Depends(get_role_service),
) -> list[RoleRead]:
    """List roles, optionally filtered by scope. Any authenticated user may read the catalog."""
    return await service.list_roles(scope)


@router.get("/{role_id}", response_model=RoleRead)
async def get_role(
    role_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: RoleService = Depends(get_role_service),
) -> RoleRead:
    """Retrieve a single role with its module permissions."""
    return await service.get_role(role_id)


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    current_user: CurrentUser = Depends(require_platform_user),
    service: RoleService = Depends(get_role_service),
) -> RoleRead:
    """Create a new role. Platform-only — roles are a shared, system-level catalog."""
    return await service.create_role(payload)


@router.patch("/{role_id}", response_model=RoleRead)
async def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    current_user: CurrentUser = Depends(require_platform_user),
    service: RoleService = Depends(get_role_service),
) -> RoleRead:
    """Update a role's metadata and/or module permissions. Platform-only."""
    return await service.update_role(role_id, payload)
