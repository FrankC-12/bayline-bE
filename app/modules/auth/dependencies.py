import uuid

import jwt
from fastapi import Depends, Request

from app.core.security import decode_access_token
from app.modules.auth.cookies import ACCESS_COOKIE
from app.modules.auth.exceptions import InsufficientPermissionsError, InvalidTokenError
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import RoleScope


def get_current_user(request: Request) -> CurrentUser:
    """Validate the access cookie; Authorization headers are not session credentials."""
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise InvalidTokenError()
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc

    try:
        return CurrentUser(
            user_id=uuid.UUID(payload["sub"]),
            email=payload["email"],
            role_id=uuid.UUID(payload["role_id"]),
            role_slug=payload["role_slug"],
            scope=RoleScope(payload["scope"]),
            holding_id=uuid.UUID(payload["holding_id"]) if payload.get("holding_id") else None,
            filial_id=uuid.UUID(payload["filial_id"]) if payload.get("filial_id") else None,
        )
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
