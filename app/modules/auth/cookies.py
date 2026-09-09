"""Session JWTs never leave the server except as HttpOnly cookies."""

from fastapi import Response

from app.core.config import get_settings

ACCESS_COOKIE = "bayline_access_token"
REFRESH_COOKIE = "bayline_refresh_token"


def set_session_cookies(response: Response, tokens) -> None:
    settings = get_settings()
    for name, value, lifetime, path in (
        (ACCESS_COOKIE, tokens.access_token, tokens.expires_in, settings.api_v1_prefix),
        (
            REFRESH_COOKIE,
            tokens.refresh_token,
            tokens.refresh_expires_in,
            f"{settings.api_v1_prefix}/auth",
        ),
    ):
        response.set_cookie(
            name,
            value,
            max_age=lifetime,
            path=path,
            secure=settings.cookie_secure,
            httponly=True,
            samesite="lax",
        )
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookies(response: Response) -> None:
    settings = get_settings()
    for name, path in (
        (ACCESS_COOKIE, settings.api_v1_prefix),
        (REFRESH_COOKIE, f"{settings.api_v1_prefix}/auth"),
    ):
        response.delete_cookie(
            name, path=path, secure=settings.cookie_secure, httponly=True, samesite="lax"
        )
    response.headers["Cache-Control"] = "no-store"
