import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_platform_user
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.permissions import ensure_module_access
from app.modules.roles.schemas import RoleCreate, RoleDirectoryEntry, RoleRead, RoleUpdate
from app.modules.roles.service import RoleService

MODULE_ID = "usuarios-accesos"

router = APIRouter(prefix="/roles", tags=["Roles"])


def get_role_service(db: AsyncSession = Depends(get_db)) -> RoleService:
    return RoleService(db)


@router.get("", response_model=list[RoleRead])
async def list_roles(
    scope: RoleScope | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: RoleService = Depends(get_role_service),
) -> list[RoleRead]:
    """List roles with their full module permission matrix. Platform and
    Holding callers administer roles by construction; at Filial scope this
    requires the "usuarios-accesos" module permission — this is the endpoint
    behind the Usuarios screen, not the role-picker dropdowns elsewhere in
    the app (those use GET /roles/directory instead)."""
    if current_user.scope == RoleScope.FILIAL:
        await ensure_module_access(service.db, current_user, current_user.filial_id, MODULE_ID, AccessLevel.VER)
    return await service.list_roles(scope)


@router.get("/directory", response_model=list[RoleDirectoryEntry])
async def list_roles_directory(
    scope: RoleScope | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: RoleService = Depends(get_role_service),
) -> list[RoleDirectoryEntry]:
    """Minimal id/name/slug/scope listing for role-picker dropdowns
    (assigning a técnico, filtering Calendario by role, etc.). Open to any
    authenticated user — it never exposes the module permission matrix, so
    it carries none of the admin-only data GET /roles does."""
    return await service.list_roles(scope)


@router.get("/{role_id}", response_model=RoleRead)
async def get_role(
    role_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_platform_user),
    service: RoleService = Depends(get_role_service),
) -> RoleRead:
    """Retrieve a single role with its module permissions. Platform-only —
    unlike the list endpoint, nothing in the app fetches a role by id for an
    ordinary dropdown, so there's no legitimate non-admin use to preserve."""
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
