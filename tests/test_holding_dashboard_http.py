"""HTTP-level ownership check for GET /holdings/{id}/dashboard — only the
owning Holding may view its own summary dashboard. The aggregation logic
itself is covered at the service level in test_holding_dashboard.py."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base, get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.holdings import router as holdings_routes
from app.modules.holdings.models import Holding
from app.modules.roles.enums import RoleScope


def _user(scope: RoleScope, *, holding_id=None, filial_id=None, role_slug="whatever") -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), email="user@test.com", role_id=uuid.uuid4(), role_slug=role_slug,
        scope=scope, holding_id=holding_id, filial_id=filial_id,
    )


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        holding_a = Holding(name="Holding A", slug="holding-a")
        holding_b = Holding(name="Holding B", slug="holding-b")
        session.add_all([holding_a, holding_b])
        session.commit()
        session.add(Filial(holding_id=holding_a.id, name="Filial A1", slug="filial-a1"))
        session.commit()

        app = FastAPI()
        app.include_router(holdings_routes.router, prefix="/api/v1")
        register_exception_handlers(app)

        db = AsyncAdapter(session)
        app.dependency_overrides[get_db] = lambda: db

        yield app, holding_a, holding_b


def _client_as(app, user: CurrentUser) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_holding_user_can_see_their_own_dashboard(env):
    app, holding_a, _hb = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_a.id)) as client:
        response = await client.get(f"/api/v1/holdings/{holding_a.id}/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert len(body["filiales"]) == 1
    assert body["filiales"][0]["clientes"] == 0


@pytest.mark.asyncio
async def test_holding_user_cannot_see_another_holdings_dashboard(env):
    app, holding_a, holding_b = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_b.id)) as client:
        response = await client.get(f"/api/v1/holdings/{holding_a.id}/dashboard")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_filial_user_cannot_see_a_holding_dashboard(env):
    app, holding_a, _hb = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=uuid.uuid4())) as client:
        response = await client.get(f"/api/v1/holdings/{holding_a.id}/dashboard")

    assert response.status_code == 403
