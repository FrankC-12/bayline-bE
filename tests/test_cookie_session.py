"""Browser session contract, CSRF protection and production configuration."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exception_handlers import register_exception_handlers
from app.core.http_security import HttpSecurityMiddleware
from app.core.security import create_access_token, create_refresh_token, decode_refresh_token
from app.modules.auth import cookies
from app.modules.auth import router as routes
from app.modules.auth.exceptions import InvalidRefreshTokenError
from app.modules.auth.schemas import TokenResponse

ORIGIN = "https://app.example.com"
HEADERS = {"Origin": ORIGIN, "X-CSRF-Protection": "1"}


@pytest.fixture
def session_app(monkeypatch):
    settings = Settings(
        _env_file=None,
        app_env="production",
        debug=False,
        allowed_origins=[ORIGIN],
        database_url="postgresql+asyncpg://unused/unused",
        secret_key="a" * 64,
        platform_admin_email="admin@example.com",
        platform_admin_password="unused",
    )
    monkeypatch.setattr(cookies, "get_settings", lambda: settings)
    monkeypatch.setattr("app.core.security.get_settings", lambda: settings)
    monkeypatch.setattr("app.core.http_security.get_settings", lambda: settings)
    claims = dict(
        sub=str(uuid.uuid4()),
        email="user@example.com",
        role_id=str(uuid.uuid4()),
        role_slug="filial-admin",
        scope="filial",
        holding_id=None,
        filial_id=str(uuid.uuid4()),
    )

    def tokens():
        return TokenResponse(
            access_token=create_access_token(claims),
            refresh_token=create_refresh_token(claims["sub"]),
            expires_in=900,
            refresh_expires_in=3600,
        )

    class FakeAuth:
        async def login(self, email, password):
            return tokens()

        async def refresh(self, token):
            try:
                decode_refresh_token(token)
            except Exception as exc:
                raise InvalidRefreshTokenError() from exc
            return tokens()

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.add_middleware(HttpSecurityMiddleware)
    register_exception_handlers(app)
    app.dependency_overrides[routes.get_auth_service] = FakeAuth
    return app


@pytest.mark.asyncio
async def test_login_me_refresh_logout_also_exposes_tokens_for_mobile(session_app):
    """Web keeps using the HttpOnly cookies (unchanged); a mobile client has
    no cookie jar, so login/refresh now ALSO return the tokens in the body —
    this is the intentional contract change that makes mobile possible."""
    async with AsyncClient(transport=ASGITransport(app=session_app), base_url=ORIGIN) as client:
        result = await client.post(
            "/api/v1/auth/login",
            headers=HEADERS,
            json={"email": "user@example.com", "password": "test"},
        )
        assert result.status_code == 200
        body = result.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"] and body["refresh_token"]
        headers = result.headers.get_list("set-cookie")
        assert len(headers) == 2
        assert all("HttpOnly" in h and "Secure" in h and "SameSite=lax" in h for h in headers)
        assert all("Domain=" not in h for h in headers)
        assert any("Path=/api/v1/auth" in h for h in headers)
        assert result.headers["cache-control"] == "no-store"
        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "user@example.com"
        assert "access_token" not in me.json()
        # A mobile client authenticates the same /me endpoint with its stored
        # access_token as a Bearer header instead of a cookie.
        bearer_me = await client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
        assert bearer_me.status_code == 200
        # An expired/missing access cookie can be recovered using the HttpOnly refresh cookie.
        for cookie in list(client.cookies.jar):
            if cookie.name == cookies.ACCESS_COOKIE:
                client.cookies.delete(cookie.name, domain=cookie.domain, path=cookie.path)
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        renewed = await client.post("/api/v1/auth/refresh", headers=HEADERS)
        assert renewed.status_code == 200
        assert renewed.json()["access_token"]
        assert (await client.get("/api/v1/auth/me")).status_code == 200
        logged_out = await client.post("/api/v1/auth/logout", headers=HEADERS)
        assert logged_out.status_code == 204
        assert all("Max-Age=0" in h for h in logged_out.headers.get_list("set-cookie"))
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await client.post("/api/v1/auth/refresh", headers=HEADERS)).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Origin": ORIGIN}])
async def test_missing_csrf_header_is_rejected_for_every_auth_endpoint(session_app, headers):
    """X-CSRF-Protection is required unconditionally — even login/refresh's
    Origin exemption (below) never waives this, since a plain HTML form
    can't set it without JS."""
    async with AsyncClient(transport=ASGITransport(app=session_app), base_url=ORIGIN) as client:
        for endpoint in ["login", "refresh", "logout"]:
            result = await client.post(
                "/api/v1/auth/" + endpoint,
                headers=headers,
                json={"email": "user@example.com", "password": "test"},
            )
            assert result.status_code == 403
            assert result.json()["errorCode"] == "csrf_rejected"
            assert not result.headers.get_list("set-cookie")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    ["https://attacker.example", "null", "https://sub.app.example.com"],
)
async def test_login_and_refresh_waive_the_origin_check_but_logout_does_not(session_app, origin):
    """A mobile client has no browser-managed Origin header to send, so
    login/refresh (the two calls a client makes before it has a Bearer token
    to rely on instead) don't require it. logout isn't exempt — a request
    there either already has a Bearer token (covered by the Bearer exemption
    tested elsewhere) or must come from a known web origin."""
    headers = {"Origin": origin, "X-CSRF-Protection": "1"}
    async with AsyncClient(transport=ASGITransport(app=session_app), base_url=ORIGIN) as client:
        for endpoint in ["login", "refresh"]:
            result = await client.post(
                "/api/v1/auth/" + endpoint,
                headers=headers,
                json={"email": "user@example.com", "password": "test"},
            )
            assert result.status_code == 200

        logout = await client.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code == 403
        assert logout.json()["errorCode"] == "csrf_rejected"


@pytest.mark.asyncio
async def test_refresh_accepts_a_body_token_and_bearer_only_authenticates_access_tokens(session_app):
    """The mobile contract: a refresh_token in the body (no cookie) renews
    the session — but a Bearer header only ever authenticates a real access
    token, never a refresh token (decode_access_token rejects type=refresh),
    so a client can't bypass auth by reusing its refresh token as a Bearer."""
    async with AsyncClient(transport=ASGITransport(app=session_app), base_url=ORIGIN) as client:
        valid_refresh = create_refresh_token(str(uuid.uuid4()))
        result = await client.post(
            "/api/v1/auth/refresh", headers=HEADERS, json={"refresh_token": valid_refresh}
        )
        assert result.status_code == 200
        assert result.json()["access_token"]
        # That refresh call also set a (valid) access cookie on this client —
        # clear it so the next call is actually testing the Bearer header
        # alone, not falling back to the cookie get_current_user prefers.
        client.cookies.clear()

        assert (
            await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + valid_refresh})
        ).status_code == 401


@pytest.mark.asyncio
async def test_production_rejects_unencrypted_api(session_app):
    async with AsyncClient(
        transport=ASGITransport(app=session_app), base_url="http://app.example.com"
    ) as client:
        result = await client.post(
            "/api/v1/auth/login",
            headers=HEADERS,
            json={"email": "user@example.com", "password": "test"},
        )
        assert result.status_code == 400
        assert result.json()["errorCode"] == "https_required"
        assert not result.headers.get_list("set-cookie")


@pytest.mark.parametrize(
    "overrides",
    [
        dict(debug=True),
        dict(secret_key="short"),
        dict(allowed_origins=["http://example.com"]),
        dict(allowed_origins=["https://*.example.com"]),
        dict(allowed_origins=["https://example.com/path"]),
    ],
)
def test_insecure_production_configuration_fails(overrides):
    values = dict(
        app_env="production",
        debug=False,
        allowed_origins=[ORIGIN],
        database_url="postgresql+asyncpg://unused/unused",
        secret_key="a" * 64,
        platform_admin_email="admin@example.com",
        platform_admin_password="unused",
    )
    values.update(overrides)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)
