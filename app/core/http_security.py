"""Protect cookie-authenticated mutations, including login, refresh and logout."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings


class HttpSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        settings = get_settings()
        if request.url.path.startswith(settings.api_v1_prefix + "/"):
            if (
                settings.cookie_secure
                and request.url.scheme != "https"
                and request.url.path != f"{settings.api_v1_prefix}/health"
            ):
                return JSONResponse(
                    {"message": "HTTPS is required.", "errorCode": "https_required"},
                    status_code=400,
                )
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                # A required non-simple header defeats HTML form CSRF. Explicit
                # Origin validation also protects login and sibling subdomains.
                if (
                    request.headers.get("origin") not in settings.allowed_origins
                    or request.headers.get("x-csrf-protection") != "1"
                ):
                    return JSONResponse(
                        {
                            "message": "Origen de solicitud no permitido.",
                            "errorCode": "csrf_rejected",
                        },
                        status_code=403,
                    )
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        return await call_next(request)
