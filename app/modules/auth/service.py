import uuid

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.modules.auth.schemas import TokenResponse
from app.modules.roles.models import Role
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


class AuthService:
    """Business logic for authenticating users and issuing access tokens."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _token_response(self, user: User) -> TokenResponse:
        if user.status != UserStatus.ACTIVO:
            raise InactiveUserError()

        role_result = await self.db.execute(select(Role).where(Role.id == user.role_id))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise InvalidCredentialsError()

        settings = get_settings()
        claims = {
            "sub": str(user.id),
            "email": user.email,
            "role_id": str(role.id),
            "role_slug": role.slug,
            "scope": role.scope.value,
            "holding_id": str(user.holding_id) if user.holding_id else None,
            "filial_id": str(user.filial_id) if user.filial_id else None,
        }
        return TokenResponse(
            access_token=create_access_token(claims),
            refresh_token=create_refresh_token(str(user.id)),
            expires_in=settings.access_token_expire_minutes * 60,
            refresh_expires_in=settings.refresh_token_expire_days * 24 * 60 * 60,
        )

    async def login(self, email: str, password: str) -> TokenResponse:
        result = await self.db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None or user.password_hash is None:
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

        return await self._token_response(user)

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
        return await self._token_response(user)
