from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(claims: dict[str, Any], expires_minutes: int | None = None) -> str:
    """Encode a JWT access token embedding the given claims."""
    settings = get_settings()
    minutes = expires_minutes or settings.access_token_expire_minutes
    expire_at = datetime.now(UTC) + timedelta(minutes=minutes)
    payload = {**claims, "type": "access", "exp": expire_at}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(subject: str, expires_days: int | None = None) -> str:
    """Create a longer-lived JWT that can only be used to renew a session."""
    settings = get_settings()
    days = expires_days or settings.refresh_token_expire_days
    expire_at = datetime.now(UTC) + timedelta(days=days)
    payload = {"sub": subject, "type": "refresh", "exp": expire_at}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT access token. Raises jwt exceptions on failure."""
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Expected an access token")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Decode a refresh JWT and reject access tokens used at the refresh endpoint."""
    settings = get_settings()
    payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    if payload.get("type") != "refresh":
        raise jwt.InvalidTokenError("Expected a refresh token")
    return payload
