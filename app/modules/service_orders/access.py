"""Shared authorization for service orders and their child resources."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel
from app.modules.roles.permissions import ensure_module_access
from app.modules.service_orders.models import ServiceOrder

MODULE_ID = "asesor-servicios"
TECNICO_MODULE_ID = "tecnico-servicio"


async def has_module_access(
    current_user: CurrentUser,
    filial_id: uuid.UUID,
    db: AsyncSession,
    module_id: str,
    level: AccessLevel = AccessLevel.VER,
) -> bool:
    try:
        await ensure_module_access(db, current_user, filial_id, module_id, level)
        return True
    except InsufficientPermissionsError:
        return False


async def require_list_access(
    current_user: CurrentUser, filial_id: uuid.UUID, db: AsyncSession
) -> uuid.UUID | None:
    """Returns None if the caller can see every order in the filial
    (asesor-servicios VER), or the caller's own user_id if they only have
    tecnico-servicio access — in which case the list must be filtered to
    orders assigned to them as técnico. Raises if neither applies."""
    if await has_module_access(current_user, filial_id, db, MODULE_ID):
        return None
    if await has_module_access(current_user, filial_id, db, TECNICO_MODULE_ID):
        return current_user.user_id
    raise InsufficientPermissionsError()


async def require_order_access(
    current_user: CurrentUser,
    order: ServiceOrder,
    db: AsyncSession,
    level: AccessLevel = AccessLevel.VER,
) -> None:
    """Same idea as require_list_access but for a single already-fetched
    order: a técnico without asesor-servicios access can still act on it —
    at the requested level — if they're the técnico assigned to it. Used
    with level=EDITAR for task status/timer changes, which a técnico needs
    to work their own assigned tasks (but not e.g. delete a task or change
    who pays for it — those stay asesor-only via the plain _ensure_access)."""
    if await has_module_access(current_user, order.filial_id, db, MODULE_ID, level):
        return
    if (
        await has_module_access(current_user, order.filial_id, db, TECNICO_MODULE_ID, level)
        and not getattr(order, "is_draft", False)
        and order.technician_user_id == current_user.user_id
    ):
        return
    raise InsufficientPermissionsError()
