import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel
from app.modules.roles.models import RoleModulePermission
from app.modules.users.models import UserModulePermission

# The Súper Administrador role always has full access within its own filial —
# it's the role that configures everyone else's permissions in the first place.
SUPER_ADMIN_SLUG = "filial-admin"

_MEETS_LEVEL: dict[AccessLevel, set[AccessLevel]] = {
    AccessLevel.VER: {AccessLevel.VER, AccessLevel.EDITAR},
    AccessLevel.EDITAR: {AccessLevel.EDITAR},
}


async def ensure_module_access(
    db: AsyncSession,
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    module_id: str,
    level: AccessLevel,
) -> None:
    """Enforces both tenant isolation (same filial) and role-based module
    permission for a request. Raises InsufficientPermissionsError if either
    check fails.

    - `filial_id` is whatever filial the request is operating on (from a
      query param or from the resource being accessed) — must match the
      caller's own filial.
    - `module_id` must be one of the slugs in roles.module_catalog.MODULE_CATALOG.
    - `level` is the minimum access the caller's role needs: AccessLevel.VER
      for reads, AccessLevel.EDITAR for anything that creates/updates/deletes.
    """
    if current_user.filial_id != filial_id:
        raise InsufficientPermissionsError()

    if current_user.role_slug == SUPER_ADMIN_SLUG:
        return

    override_result = await db.execute(
        select(UserModulePermission).where(
            UserModulePermission.user_id == current_user.user_id,
            UserModulePermission.module_id == module_id,
        )
    )
    override = override_result.scalar_one_or_none()
    if override is not None:
        if override.access not in _MEETS_LEVEL[level]:
            raise InsufficientPermissionsError()
        return

    result = await db.execute(
        select(RoleModulePermission).where(
            RoleModulePermission.role_id == current_user.role_id,
            RoleModulePermission.module_id == module_id,
        )
    )
    permission = result.scalar_one_or_none()
    if permission is None or permission.access not in _MEETS_LEVEL[level]:
        raise InsufficientPermissionsError()
