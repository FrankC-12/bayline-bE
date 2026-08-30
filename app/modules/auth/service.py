from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import create_access_token, verify_password
from app.modules.auth.exceptions import InactiveUserError, InvalidCredentialsError
from app.modules.auth.schemas import TokenResponse
from app.modules.roles.models import Role
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


class AuthService:
    """Business logic for authenticating users and issuing access tokens."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def login(self, email: str, password: str) -> TokenResponse:
        result = await self.db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None or user.password_hash is None:
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

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
        access_token = create_access_token(claims)

        return TokenResponse(
            access_token=access_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )
