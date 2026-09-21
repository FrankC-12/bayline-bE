"""Read endpoints on roles/holdings/filiales used to accept any authenticated
caller — a filial técnico could fetch any holding or filial by id (no
ownership check at all) or read a role's full module-permission map. The
mutating endpoints (POST/PATCH/activate/deactivate) were already correctly
gated; this locks down the matching GETs the same way. The roles LIST
endpoint now requires the "usuarios-accesos" module permission for filial
callers (holding/platform administer roles by construction) — real
filial-level screens that need role-picker dropdowns (Calendario, crear
ODS, etc.) use GET /roles/directory instead, which stays open to any
authenticated user and never carries the permission map."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales import router as filiales_routes
from app.modules.filiales.models import Filial
from app.modules.holdings import router as holdings_routes
from app.modules.holdings.models import Holding
from app.modules.roles import router as roles_routes
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission


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
        filial_a = Filial(holding_id=holding_a.id, name="Filial A1", slug="filial-a1")
        session.add(filial_a)
        role = Role(name="Asesor", slug="asesor", scope=RoleScope.FILIAL)
        session.add(role)
        session.commit()
        session.add(RoleModulePermission(role_id=role.id, module_id="asesor-servicios", access=AccessLevel.EDITAR))
        session.commit()

        app = FastAPI()
        app.include_router(roles_routes.router, prefix="/api/v1")
        app.include_router(holdings_routes.router, prefix="/api/v1")
        app.include_router(filiales_routes.router, prefix="/api/v1")
        register_exception_handlers(app)

        db = AsyncAdapter(session)
        app.dependency_overrides[roles_routes.get_role_service] = lambda: roles_routes.RoleService(db)
        app.dependency_overrides[holdings_routes.get_holding_service] = lambda: holdings_routes.HoldingService(db)
        app.dependency_overrides[filiales_routes.get_filial_service] = lambda: filiales_routes.FilialService(db)

        yield app, session, holding_a, holding_b, filial_a, role


def _client_as(app, user: CurrentUser) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- Roles -------------------------------------------------------------


@pytest.mark.asyncio
async def test_ordinary_filial_role_cannot_list_roles(env):
    app, _session, _ha, _hb, _fa, _role = env
    filial_id = uuid.uuid4()
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_id)) as client:
        response = await client.get("/api/v1/roles")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_ordinary_filial_role_can_still_use_the_role_directory(env):
    app, _session, _ha, _hb, _fa, _role = env
    filial_id = uuid.uuid4()
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_id)) as client:
        response = await client.get("/api/v1/roles/directory")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "permissions" not in body[0]
    assert body[0]["slug"] == "asesor"


@pytest.mark.asyncio
async def test_holding_user_sees_full_permissions_in_role_list(env):
    app, _session, holding_a, _hb, _fa, _role = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_a.id)) as client:
        response = await client.get("/api/v1/roles")

    body = response.json()
    assert len(body[0]["permissions"]) == 1
    assert body[0]["permissions"][0]["module_id"] == "asesor-servicios"


@pytest.mark.asyncio
async def test_platform_user_sees_full_permissions_in_role_list(env):
    app, _session, _ha, _hb, _fa, _role = env
    async with _client_as(app, _user(RoleScope.PLATFORM)) as client:
        response = await client.get("/api/v1/roles")

    assert len(response.json()[0]["permissions"]) == 1


@pytest.mark.asyncio
async def test_filial_user_cannot_fetch_a_single_role(env):
    app, _session, _ha, _hb, _fa, role = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=uuid.uuid4())) as client:
        response = await client.get(f"/api/v1/roles/{role.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_platform_user_can_fetch_a_single_role(env):
    app, _session, _ha, _hb, _fa, role = env
    async with _client_as(app, _user(RoleScope.PLATFORM)) as client:
        response = await client.get(f"/api/v1/roles/{role.id}")

    assert response.status_code == 200


# --- Holdings ------------------------------------------------------------


@pytest.mark.asyncio
async def test_filial_user_cannot_list_holdings(env):
    app, _session, _ha, _hb, _fa, _role = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=uuid.uuid4())) as client:
        response = await client.get("/api/v1/holdings")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_holding_user_cannot_list_holdings(env):
    """Holdings are enumerable only by platform — a holding admin manages
    their own holding, not the whole tenant list."""
    app, _session, holding_a, _hb, _fa, _role = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_a.id)) as client:
        response = await client.get("/api/v1/holdings")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_platform_user_can_list_holdings(env):
    app, _session, holding_a, holding_b, _fa, _role = env
    async with _client_as(app, _user(RoleScope.PLATFORM)) as client:
        response = await client.get("/api/v1/holdings")

    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_holding_user_cannot_fetch_any_holding_by_id(env):
    app, _session, holding_a, _hb, _fa, _role = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_a.id)) as client:
        response = await client.get(f"/api/v1/holdings/{holding_a.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_platform_user_can_fetch_any_holding_by_id(env):
    app, _session, holding_a, _hb, _fa, _role = env
    async with _client_as(app, _user(RoleScope.PLATFORM)) as client:
        response = await client.get(f"/api/v1/holdings/{holding_a.id}")

    assert response.status_code == 200


# --- Filiales --------------------------------------------------------------


@pytest.mark.asyncio
async def test_filial_user_cannot_list_filiales(env):
    app, _session, _ha, _hb, _fa, _role = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=uuid.uuid4())) as client:
        response = await client.get("/api/v1/filiales")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_holding_user_lists_only_their_own_filiales(env):
    app, _session, holding_a, _hb, filial_a, _role = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_a.id)) as client:
        response = await client.get("/api/v1/filiales")

    body = response.json()
    assert [f["id"] for f in body] == [str(filial_a.id)]


@pytest.mark.asyncio
async def test_holding_user_cannot_fetch_another_holdings_filial(env):
    app, _session, _ha, holding_b, filial_a, _role = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_b.id)) as client:
        response = await client.get(f"/api/v1/filiales/{filial_a.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_holding_user_can_fetch_their_own_filial(env):
    app, _session, holding_a, _hb, filial_a, _role = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_a.id)) as client:
        response = await client.get(f"/api/v1/filiales/{filial_a.id}")

    assert response.status_code == 200
