"""Holding administration context. Identity remains the actual holding user."""

import uuid
from sqlalchemy import select
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.roles.enums import RoleScope


async def apply_holding_context(db, user: CurrentUser, filial_id: uuid.UUID) -> CurrentUser:
    if user.scope != RoleScope.HOLDING or user.holding_id is None:
        raise InsufficientPermissionsError()
    filial = (
        await db.execute(
            select(Filial).where(
                Filial.id == filial_id,
                Filial.holding_id == user.holding_id,
                Filial.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if filial is None:
        raise InsufficientPermissionsError()
    return user.model_copy(
        update={
            "scope": RoleScope.FILIAL,
            "role_slug": "filial-admin",
            "filial_id": filial.id,
            "holding_context": True,
            "filial_name": filial.name,
        }
    )
