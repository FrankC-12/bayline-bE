"""Authorize the ODS even when its preliminary inspection does not exist."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.inspections.router import router


@pytest.mark.asyncio
async def test_empty_preliminary_inspection_lookup_requires_order_access(billing_db):
    ctx = billing_db
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    user = CurrentUser(
        user_id=uuid.uuid4(),
        email="admin@example.com",
        role_id=uuid.uuid4(),
        role_slug="filial-admin",
        scope="filial",
        filial_id=ctx.filial_id,
        holding_id=None,
    )
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    path = f"/api/v1/inspections/by-order/{ctx.order_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        authorized = await http.get(path)
        assert authorized.status_code == 200
        assert authorized.json() is None
        user = user.model_copy(update={"filial_id": uuid.uuid4()})
        assert (await http.get(path)).status_code == 403
        user = user.model_copy(update={"filial_id": ctx.filial_id, "role_slug": "sin-permiso"})
        assert (await http.get(path)).status_code == 403
        user = user.model_copy(update={"role_slug": "filial-admin"})
        assert (await http.get(f"/api/v1/inspections/by-order/{uuid.uuid4()}")).status_code == 404
