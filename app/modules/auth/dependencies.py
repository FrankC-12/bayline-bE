import uuid

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.modules.users.models import User
from app.modules.users.enums import UserStatus
from app.modules.roles.models import Role

from app.core.audit_logging import set_actor
from app.core.security import decode_access_token
from app.modules.auth.cookies import ACCESS_COOKIE
from app.modules.auth.exceptions import (
    InactiveUserError,
    InsufficientPermissionsError,
    InvalidTokenError,
)
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import RoleScope


api_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request, _bearer=Depends(api_bearer), db: AsyncSession = Depends(get_db)
) -> CurrentUser:
    """Validate the access cookie (web) or an `Authorization: Bearer` token
    (mobile/native clients, which have no browser cookie jar to rely on) —
    whichever the request actually sent. See also HttpSecurityMiddleware,
    which exempts Bearer-only requests from the Origin check while still
    requiring the CSRF protection header."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
    if not token:
        raise InvalidTokenError()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc

    try:
        user = CurrentUser(
            user_id=uuid.UUID(payload["sub"]),
            email=payload["email"],
            full_name=payload.get("full_name", ""),
            role_id=uuid.UUID(payload["role_id"]),
            role_slug=payload["role_slug"],
            scope=RoleScope(payload["scope"]),
            holding_id=uuid.UUID(payload["holding_id"]) if payload.get("holding_id") else None,
            filial_id=uuid.UUID(payload["filial_id"]) if payload.get("filial_id") else None,
        )
        # A signed JWT is not enough: suspension must invalidate existing web
        # cookies and mobile Bearer tokens on their next request.
        record = (
            await db.execute(
                select(User, Role)
                .join(Role, Role.id == User.role_id)
                .where(User.id == user.user_id)
            )
        ).one_or_none()
        if record is None:
            raise InvalidTokenError()
        account, role = record
        if account.status != UserStatus.ACTIVO:
            raise InactiveUserError()
        if payload.get("session_version", 0) != account.session_version:
            raise InvalidTokenError()
        # Tenant and role permissions always follow the current database values.
        user = CurrentUser(
            user_id=account.id,
            email=account.email,
            full_name=account.full_name,
            role_id=role.id,
            role_slug=role.slug,
            scope=role.scope,
            holding_id=account.holding_id,
            filial_id=account.filial_id,
        )
        set_actor(user.user_id, user.filial_id, user.holding_id)
        return user
    except (KeyError, ValueError) as exc:
        raise InvalidTokenError() from exc


def require_platform_user(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Allow only Platform-scoped callers."""
    if current_user.scope != RoleScope.PLATFORM:
        raise InsufficientPermissionsError()
    return current_user


def require_holding_user(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Allow only Holding-scoped callers."""
    if current_user.scope != RoleScope.HOLDING:
        raise InsufficientPermissionsError()
    return current_user


def require_filial_admin(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Allow only the 'Súper Administrador' (filial-admin) role."""
    if current_user.scope != RoleScope.FILIAL or current_user.role_slug != "filial-admin":
        raise InsufficientPermissionsError()
    return current_user
