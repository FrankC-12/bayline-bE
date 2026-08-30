import uuid

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token
from app.modules.auth.exceptions import InsufficientPermissionsError, InvalidTokenError
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import RoleScope

bearer_scheme = HTTPBearer(auto_error=True)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> CurrentUser:
    """Decode and validate the Bearer token, returning the authenticated caller's claims."""
    try:
        payload = decode_access_token(credentials.credentials)
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
