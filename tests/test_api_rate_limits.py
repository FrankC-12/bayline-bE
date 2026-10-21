"""HTTP policy: identity, route normalization, bypasses and rejection contracts."""

import uuid

import pytest
from fastapi import APIRouter, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import OperationalError
from starlette.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.rate_limiting import RateLimitMiddleware
from app.core.security import create_access_token
from app.modules.auth.cookies import ACCESS_COOKIE


class RecordingLimiter:
    def __init__(self):
        self.calls = []
        self.counts = {}
        self.error = None

    async def check(self, budgets):
        self.calls.append(budgets)
        if self.error:
            raise self.error
        rejected = False
        for budget in budgets:
            self.counts[budget.key] = self.counts.get(budget.key, 0) + 1
            rejected |= self.counts[budget.key] > budget.limit
        return 42 if rejected else None


def setup_app(**settings):
    app = FastAPI()
    limiter = RecordingLimiter()
    config = get_settings().model_copy(update=settings)
    app.add_middleware(RateLimitMiddleware, limiter=limiter, settings=config)
    app.add_middleware(
        CORSMiddleware, allow_origins=["http://localhost:3000"], expose_headers=["Retry-After"]
    )
    reached = []

    @app.api_route("/api/v1/items/{item_id}", methods=["GET", "POST"])
    async def item(item_id: str, request: Request):
        reached.append(item_id)
        return {"id": item_id}

    @app.post("/api/v1/uploads")
    async def upload():
        return {"ok": True}

    @app.post("/api/v1/auth/refresh")
    async def refresh():
        return {"ok": True}

    @app.get("/api/v1/health")
    async def health():
        return {"ok": True}

    @app.get("/outside")
    async def outside():
        return {"ok": True}

    return app, limiter, reached


def client(app, ip="192.0.2.1"):
    return AsyncClient(transport=ASGITransport(app=app, client=(ip, 123)), base_url="http://test")


@pytest.mark.asyncio
async def test_query_and_id_changes_share_budget_and_429_has_cors_and_retry_header():
    app, limiter, reached = setup_app(rate_limit_read_per_minute=2)
    async with client(app) as http:
        assert (await http.get("/api/v1/items/first?q=1")).status_code == 200
        assert (await http.get("/api/v1/items/second?q=2")).status_code == 200
        response = await http.get(
            "/api/v1/items/third?q=3", headers={"Origin": "http://localhost:3000"}
        )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "42"
    assert response.json()["errorCode"] == "rate_limited"
    assert "Retry-After" in response.headers["access-control-expose-headers"]
    assert reached == ["first", "second"]
    assert len(limiter.counts) == 3


@pytest.mark.asyncio
async def test_signed_user_shares_budget_across_ip_cookie_and_new_tokens():
    app, _, _ = setup_app(rate_limit_user_per_minute=1)
    user_id = str(uuid.uuid4())
    first = create_access_token({"sub": user_id})
    renewed = create_access_token({"sub": user_id, "nonce": "new"})
    async with client(app) as http:
        assert (
            await http.get("/api/v1/items/1", headers={"Authorization": f"Bearer {first}"})
        ).status_code == 200
    async with client(app, "192.0.2.2") as http:
        http.cookies.set(ACCESS_COOKIE, renewed)
        assert (await http.get("/api/v1/items/2")).status_code == 429


@pytest.mark.asyncio
async def test_forwarded_headers_and_forged_token_do_not_change_anonymous_budget():
    app, _, _ = setup_app(rate_limit_anonymous_per_minute=1)
    async with client(app) as http:
        assert (await http.get("/api/v1/items/1")).status_code == 200
        response = await http.get(
            "/api/v1/items/2",
            headers={
                "X-Forwarded-For": "203.0.113.1",
                "Authorization": "Bearer forged.token.value",
            },
        )
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_users_share_outer_ip_budget_even_with_different_valid_tokens():
    app, _, _ = setup_app(rate_limit_ip_per_minute=1)
    async with client(app) as http:
        for expected in [200, 429]:
            token = create_access_token({"sub": str(uuid.uuid4())})
            response = await http.get(
                "/api/v1/items/1", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == expected


@pytest.mark.asyncio
async def test_write_upload_and_auth_have_stricter_budgets():
    app, limiter, _ = setup_app()
    async with client(app) as http:
        await http.post("/api/v1/items/1")
        assert any(b.key.startswith("write:") and b.limit == 60 for b in limiter.calls[-1])
        await http.post("/api/v1/uploads")
        assert any(b.key.startswith("expensive:") and b.limit == 20 for b in limiter.calls[-1])
        await http.post("/api/v1/auth/refresh")
        assert any(b.key.startswith("auth:") and b.limit == 30 for b in limiter.calls[-1])


@pytest.mark.asyncio
async def test_health_preflight_and_non_api_paths_do_not_use_storage():
    app, limiter, _ = setup_app()
    async with client(app) as http:
        await http.get("/api/v1/health")
        await http.options("/api/v1/items/1")
        await http.get("/outside")
    assert limiter.calls == []


@pytest.mark.asyncio
async def test_storage_failure_blocks_endpoint_instead_of_disabling_protection():
    app, limiter, reached = setup_app()
    limiter.error = OperationalError("test", {}, Exception("unavailable"))
    async with client(app) as http:
        response = await http.get("/api/v1/items/1")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert reached == []


@pytest.mark.asyncio
async def test_explicit_disable_skips_storage():
    app, limiter, _ = setup_app(rate_limit_enabled=False)
    async with client(app) as http:
        assert (await http.get("/api/v1/items/1")).status_code == 200
    assert limiter.calls == []


@pytest.mark.asyncio
async def test_included_router_nested_endpoints_share_resource_budget():
    app, _, _ = setup_app(rate_limit_read_per_minute=1)
    router = APIRouter()

    @router.get("/orders/{order_id}/summary")
    async def summary(order_id: str):
        return {"id": order_id}

    @router.get("/orders/{order_id}/tasks")
    async def tasks(order_id: str):
        return []

    app.include_router(router, prefix="/api/v1")
    async with client(app) as http:
        assert (await http.get("/api/v1/orders/1/summary")).status_code == 200
        assert (await http.get("/api/v1/orders/2/tasks")).status_code == 429


@pytest.mark.asyncio
async def test_rejected_upload_does_not_read_request_body():
    app, limiter, _ = setup_app(rate_limit_expensive_per_minute=1)
    async with client(app) as http:
        assert (await http.post("/api/v1/uploads")).status_code == 200
    messages = []

    async def receive():
        raise AssertionError("Rejected request must not read or buffer upload bytes")

    async def send(message):
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/uploads",
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "client": ("192.0.2.1", 123),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    assert messages[0]["status"] == 429
    assert len(limiter.calls) == 2
