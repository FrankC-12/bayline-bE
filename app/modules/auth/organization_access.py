"""Live organization checks and transactional session invalidation."""

from datetime import UTC, datetime
from sqlalchemy import select, update
from app.core.exceptions import UnauthorizedError
from app.modules.filiales.models import Filial
from app.modules.holdings.models import Holding
from app.modules.users.models import User
from app.modules.users.enums import UserStatus
from app.modules.auth.models import RefreshSession, RecoveryChallenge
from app.modules.auth.exceptions import InactiveUserError


async def ensure_account_available(db, user):
    if user.status != UserStatus.ACTIVO:
        raise InactiveUserError()
    holding_id = user.holding_id
    if user.filial_id:
        filial = (
            await db.execute(select(Filial).where(Filial.id == user.filial_id))
        ).scalar_one_or_none()
        if (
            filial is None
            or not filial.is_active
            or (holding_id and holding_id != filial.holding_id)
        ):
            raise UnauthorizedError(
                "Tu sucursal está suspendida. Contacta a su administrador.",
                error_code="inactive_organization",
            )
        holding_id = filial.holding_id
    if holding_id:
        holding = (
            await db.execute(select(Holding).where(Holding.id == holding_id))
        ).scalar_one_or_none()
        if holding is None or not holding.is_active:
            raise UnauthorizedError(
                "El holding está suspendido y sus sucursales no tienen acceso. Contacta a la administración de la plataforma.",
                error_code="inactive_organization",
            )


async def revoke_accounts(db, user_ids):
    now = datetime.now(UTC)
    await db.execute(
        update(User).where(User.id.in_(user_ids)).values(session_version=User.session_version + 1)
    )
    await db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id.in_(user_ids), RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await db.execute(
        update(RecoveryChallenge)
        .where(RecoveryChallenge.user_id.in_(user_ids), RecoveryChallenge.consumed_at.is_(None))
        .values(consumed_at=now)
    )
