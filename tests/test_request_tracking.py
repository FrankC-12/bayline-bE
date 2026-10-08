import logging
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import BadRequestError
from app.core.request_tracking import RequestTrackingMiddleware


@pytest.mark.asyncio
async def test_request_ids_cover_success_validation_domain_and_unexpected_errors(caplog):
    app = FastAPI()
    app.add_middleware(RequestTrackingMiddleware)
    register_exception_handlers(app)

    @app.get("/test/{number}")
    async def route(number: int):
        if number == 1:
            raise BadRequestError("Datos inválidos")
        if number == 2:
            raise RuntimeError("private-token-and-client-data")
        return {"ok": True}

    caplog.set_level(logging.INFO)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as http:
        ids = []
        for value, expected in ((0, 200), (1, 400), (2, 500), ("invalid", 422)):
            response = await http.get(
                f"/test/{value}?token=secret-query",
                headers={"Authorization": "Bearer secret-auth", "X-Request-ID": "forged-id"},
            )
            assert response.status_code == expected
            identifier = response.headers["x-request-id"]
            assert uuid.UUID(identifier)
            ids.append(identifier)
            if expected != 200:
                assert response.json()["requestId"] == identifier
        assert len(set(ids)) == 4
    app_logs = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app."))
    assert "private-token-and-client-data" not in app_logs
    assert "secret-query" not in app_logs and "secret-auth" not in app_logs
    assert "exception_type=RuntimeError" in caplog.text
    assert "route=/test/{number}" in caplog.text


@pytest.mark.asyncio
async def test_bearer_header_cannot_bypass_origin_checks_when_cookie_is_used():
    from app.core.config import get_settings
    from app.core.http_security import HttpSecurityMiddleware
    from app.modules.auth.cookies import ACCESS_COOKIE

    app = FastAPI()
    app.add_middleware(HttpSecurityMiddleware)
    app.add_middleware(RequestTrackingMiddleware)

    @app.post("/api/v1/test")
    async def mutation():
        return {"ok": True}

    base = "https://test" if get_settings().cookie_secure else "http://test"
    async with AsyncClient(transport=ASGITransport(app=app), base_url=base) as http:
        response = await http.post(
            "/api/v1/test",
            headers={
                "Origin": "https://untrusted.invalid",
                "X-CSRF-Protection": "1",
                "Authorization": "Bearer ignored",
                "Cookie": f"{ACCESS_COOKIE}=session",
            },
        )
        assert response.status_code == 403
        assert response.headers["x-request-id"]
        mobile = await http.post(
            "/api/v1/test", headers={"X-CSRF-Protection": "1", "Authorization": "Bearer mobile"}
        )
        assert mobile.status_code == 200
