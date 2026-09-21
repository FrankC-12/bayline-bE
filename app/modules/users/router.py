import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.permissions import ensure_module_access
from app.modules.roles.service import RoleService
from app.modules.users.schemas import UserCreate, UserDirectoryEntry, UserRead, UserUpdate
from app.modules.users.service import UserService

MODULE_ID = "usuarios-accesos"

router = APIRouter(prefix="/users", tags=["Users"])


def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(db)


def get_role_service(db: AsyncSession = Depends(get_db)) -> RoleService:
    return RoleService(db)


async def _authorize_user_access(
    role_id: uuid.UUID,
    holding_id: uuid.UUID | None,
    filial_id: uuid.UUID | None,
    current_user: CurrentUser,
    role_service: RoleService,
) -> None:
    """Authorize reading/creating/updating a user, based on the target
    role's scope and tenant ownership. Used for both GET /{id} and the
    mutating endpoints — the same "could you manage this user" boundary
    also gates whether you may look at them individually."""
    role = await role_service.get_role(role_id)

    if role.scope == RoleScope.PLATFORM:
        if current_user.scope != RoleScope.PLATFORM:
            raise InsufficientPermissionsError()
        return

    if role.scope == RoleScope.HOLDING:
        # Only Platform onboards a holding's first admin user.
        if current_user.scope != RoleScope.PLATFORM:
            raise InsufficientPermissionsError()
        return

    # role.scope == RoleScope.FILIAL
    if current_user.scope == RoleScope.HOLDING and holding_id and current_user.holding_id == holding_id:
        return

    if (
        current_user.scope == RoleScope.FILIAL
        and current_user.role_slug == "filial-admin"
        and filial_id
        and current_user.filial_id == filial_id
    ):
        return

    raise InsufficientPermissionsError()


@router.get("", response_model=list[UserRead])
async def list_users(
    holding_id: uuid.UUID | None = Query(default=None),
    filial_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> list[UserRead]:
    """List users with full admin data (email, permission overrides).
    Platform and Holding callers administer users by construction; at Filial
    scope this requires the "usuarios-accesos" module permission — this is
    the endpoint behind the Usuarios screen, not the picker dropdowns
    elsewhere in the app (those use GET /users/directory instead)."""
    if current_user.scope == RoleScope.HOLDING:
        holding_id = current_user.holding_id
    elif current_user.scope == RoleScope.FILIAL:
        filial_id = current_user.filial_id
        await ensure_module_access(service.db, current_user, filial_id, MODULE_ID, AccessLevel.VER)
    return await service.list_users(holding_id, filial_id)


@router.get("/directory", response_model=list[UserDirectoryEntry])
async def list_users_directory(
    holding_id: uuid.UUID | None = Query(default=None),
    filial_id: uuid.UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
) -> list[UserDirectoryEntry]:
    """Minimal id/name/role listing for "asignar técnico/asesor" pickers
    across Calendario, ODS, Concesionario, etc. Open to any authenticated
    user within their own tenant — no admin permission required, since it
    never exposes email or permission overrides."""
    if current_user.scope == RoleScope.HOLDING:
        holding_id = current_user.holding_id
    elif current_user.scope == RoleScope.FILIAL:
        filial_id = current_user.filial_id
    return await service.list_users(holding_id, filial_id)


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
    role_service: RoleService = Depends(get_role_service),
) -> UserRead:
    """Retrieve a single user by id. Same tenant/scope boundary as creating
    or updating one — nothing in the app fetches a bare user by id outside
    the admin screen, so there's no dropdown-style use to preserve here."""
    user = await service.get_user(user_id)
    await _authorize_user_access(user.role_id, user.holding_id, user.filial_id, current_user, role_service)
    return user


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
    role_service: RoleService = Depends(get_role_service),
) -> UserRead:
    """Create a new user. Authorization depends on the target role's scope and tenant ownership."""
    await _authorize_user_access(
        payload.role_id, payload.holding_id, payload.filial_id, current_user, role_service
    )
    return await service.create_user(payload)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserService = Depends(get_user_service),
    role_service: RoleService = Depends(get_role_service),
) -> UserRead:
    """Update a user. Authorization depends on the target role's scope and tenant ownership."""
    existing = await service.get_user(user_id)
    target_role_id = payload.role_id or existing.role_id
    target_holding_id = payload.holding_id if payload.holding_id is not None else existing.holding_id
    target_filial_id = payload.filial_id if payload.filial_id is not None else existing.filial_id
    await _authorize_user_access(
        target_role_id, target_holding_id, target_filial_id, current_user, role_service
    )
    return await service.update_user(user_id, payload)
