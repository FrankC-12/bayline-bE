"""Protect cookie-authenticated mutations, including login, refresh and
logout.

A request authenticating with an Authorization: Bearer header instead
(mobile/native clients) is exempt from the Origin check below — classic CSRF
relies on the browser silently attaching the victim's cookies to a forged
cross-origin request; it has no way to also forge an Authorization header it
doesn't know the value of, so Origin validation doesn't protect anything for
a Bearer-authenticated call and would only break mobile, which has no
browser-managed Origin header to send in the first place. The
X-CSRF-Protection header is still required even then — a plain HTML form
can't set a custom header without JS, so it still defeats basic form-based
CSRF on its own; a mobile app can set it trivially since it isn't subject to
CORS at all.

/auth/login and /auth/refresh get the same Origin exemption even WITHOUT a
Bearer token, since logging in or refreshing is exactly how a mobile client
gets its first token — it can't present one yet on that very call. They
still require X-CSRF-Protection for the same reason as above."""

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
                has_bearer_token = request.headers.get("authorization", "").lower().startswith("bearer ")
                is_token_endpoint = request.url.path in {
                    f"{settings.api_v1_prefix}/auth/login",
                    f"{settings.api_v1_prefix}/auth/refresh",
                }
                origin_ok = (
                    has_bearer_token
                    or is_token_endpoint
                    or request.headers.get("origin") in settings.allowed_origins
                )
                # A required non-simple header defeats HTML form CSRF —
                # enforced unconditionally, even for Bearer/token-endpoint
                # calls, since it costs mobile nothing to send.
                if not origin_ok or request.headers.get("x-csrf-protection") != "1":
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
