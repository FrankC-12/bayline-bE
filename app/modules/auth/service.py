import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_logging import set_actor
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    verify_password,
)
from app.modules.auth.exceptions import (
    InactiveUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.modules.auth.models import LoginBucket, RefreshSession
from app.modules.auth.schemas import TokenResponse
from app.modules.roles.models import Role
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


class AuthService:
    """Business logic for authenticating users and issuing access tokens."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _token_response(self, user: User, *, record_login: bool = False) -> TokenResponse:
        if user.status != UserStatus.ACTIVO:
            raise InactiveUserError()

        role_result = await self.db.execute(select(Role).where(Role.id == user.role_id))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise InvalidCredentialsError()

        set_actor(user.id, user.filial_id, user.holding_id)
        settings = get_settings()
        claims = {
            "sub": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role_id": str(role.id),
            "role_slug": role.slug,
            "scope": role.scope.value,
            "holding_id": str(user.holding_id) if user.holding_id else None,
            "filial_id": str(user.filial_id) if user.filial_id else None,
        }
        tokens = TokenResponse(
            access_token=create_access_token(claims),
            refresh_token=create_refresh_token(str(user.id)),
            expires_in=settings.access_token_expire_minutes * 60,
            refresh_expires_in=settings.refresh_token_expire_days * 24 * 60 * 60,
        )

        if record_login:
            from app.modules.filiales.models import Filial
            from app.modules.holdings.control_models import HoldingAuditEvent

            holding_id = user.holding_id
            if user.filial_id:
                holding_id = (
                    await self.db.execute(
                        select(Filial.holding_id).where(Filial.id == user.filial_id)
                    )
                ).scalar()
            if holding_id:
                self.db.add(
                    HoldingAuditEvent(
                        holding_id=holding_id,
                        filial_id=user.filial_id,
                        user_id=user.id,
                        action="login",
                        entity="users",
                        resource_id=str(user.id),
                        changed_fields=[],
                    )
                )

        self.db.add(
            RefreshSession(
                token_hash=hashlib.sha256(tokens.refresh_token.encode()).hexdigest(),
                user_id=user.id,
                expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
            )
        )
        await self.db.commit()
        return tokens

    async def check_login_budget(self, email: str, ip: str) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from app.core.exceptions import DomainError

        now = datetime.now(UTC)
        await self.db.execute(delete(LoginBucket).where(LoginBucket.expires_at < now))
        await self.db.execute(delete(RefreshSession).where(RefreshSession.expires_at < now))
        window = int(now.timestamp()) // 900
        counts = []
        for kind, value, limit in (("ip", ip, 100), ("email", email.strip().casefold(), 10)):
            key = hashlib.sha256(f"{kind}:{window}:{value}".encode()).hexdigest()
            statement = insert(LoginBucket).values(
                id=key, attempts=1, expires_at=now + timedelta(minutes=15)
            )
            statement = statement.on_conflict_do_update(
                index_elements=[LoginBucket.id], set_={"attempts": LoginBucket.attempts + 1}
            ).returning(LoginBucket.attempts)
            count = (await self.db.execute(statement)).scalar_one()
            counts.append((count, limit))
            if count > limit:
                break
        await self.db.commit()
        if any(count > limit for count, limit in counts):
            error = DomainError(
                "Demasiados intentos. Intenta nuevamente en 15 minutos.",
                error_code="login_rate_limited",
            )
            error.status_code = 429
            raise error

    async def login(self, email: str, password: str) -> TokenResponse:
        result = await self.db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None or user.password_hash is None:
            # Match the expensive password check even for unknown accounts.
            verify_password(
                password, "$2b$12$w0Q5YfAGo0IRjZFnYQoQce4yDvf5RnpS18.6OxxZJGWlYF4sWuS7O"
            )
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

        return await self._token_response(user, record_login=True)

    async def refresh(self, refresh_token: str) -> TokenResponse:
        try:
            payload = decode_refresh_token(refresh_token)
            user_id = uuid.UUID(payload["sub"])
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise InvalidRefreshTokenError() from exc

        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise InvalidRefreshTokenError()
        digest = hashlib.sha256(refresh_token.encode()).hexdigest()
        session = (
            await self.db.execute(
                select(RefreshSession).where(RefreshSession.token_hash == digest).with_for_update()
            )
        ).scalar_one_or_none()
        if (
            session is None
            or session.revoked_at is not None
            or (
                session.expires_at.replace(tzinfo=UTC)
                if session.expires_at.tzinfo is None
                else session.expires_at
            )
            <= datetime.now(UTC)
        ):
            raise InvalidRefreshTokenError()
        session.revoked_at = datetime.now(UTC)
        return await self._token_response(user)

    async def logout(self, refresh_token: str, device_id: uuid.UUID | None = None) -> None:
        digest = hashlib.sha256(refresh_token.encode()).hexdigest()
        session = (
            await self.db.execute(
                select(RefreshSession).where(RefreshSession.token_hash == digest).with_for_update()
            )
        ).scalar_one_or_none()
        if session is not None:
            session.revoked_at = session.revoked_at or datetime.now(UTC)
            if device_id:
                from app.modules.notifications.models import Device

                device = await self.db.get(Device, device_id)
                if device is not None and device.user_id == session.user_id:
                    device.enabled = False
            await self.db.commit()
