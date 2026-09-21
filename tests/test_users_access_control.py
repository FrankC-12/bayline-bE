"""GET /users/{id} used to have zero authorization — any authenticated
caller, from any holding/filial, could fetch any other user's full record
by id. GET /users (list) now requires the "usuarios-accesos" module
permission for filial callers (holding/platform administer users by
construction) — ordinary "asignar técnico/asesor" dropdowns across the app
use GET /users/directory instead, which stays open to any authenticated
filial user but only ever exposes id/name/role, never email or permission
overrides."""

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
from app.modules.filiales.models import Filial
from app.modules.holdings.models import Holding
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.users import router as users_routes
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


def _user(scope: RoleScope, *, holding_id=None, filial_id=None, role_slug="whatever", role_id=None) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), email="caller@test.com", role_id=role_id or uuid.uuid4(), role_slug=role_slug,
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
        filial_b = Filial(holding_id=holding_b.id, name="Filial B1", slug="filial-b1")
        session.add_all([filial_a, filial_b])
        asesor_role = Role(name="Asesor", slug="asesor", scope=RoleScope.FILIAL)
        admin_role = Role(name="Filial Admin", slug="filial-admin", scope=RoleScope.FILIAL)
        holding_role = Role(name="Holding", slug="holding", scope=RoleScope.HOLDING)
        session.add_all([asesor_role, admin_role, holding_role])
        session.commit()
        target = User(
            full_name="Juan Pérez", email="juan@filial-a1.com", status=UserStatus.ACTIVO,
            role_id=asesor_role.id, filial_id=filial_a.id,
        )
        session.add(target)
        session.commit()

        app = FastAPI()
        app.include_router(users_routes.router, prefix="/api/v1")
        register_exception_handlers(app)

        db = AsyncAdapter(session)
        app.dependency_overrides[users_routes.get_user_service] = lambda: users_routes.UserService(db)
        app.dependency_overrides[users_routes.get_role_service] = lambda: users_routes.RoleService(db)

        yield app, session, holding_a, holding_b, filial_a, filial_b, asesor_role, admin_role, holding_role, target


def _client_as(app, user: CurrentUser) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_filial_user_cannot_fetch_a_user_from_another_filial(env):
    app, _s, _ha, _hb, _fa, filial_b, _ar, _adr, _hr, target = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_b.id, role_slug="asesor")) as client:
        response = await client.get(f"/api/v1/users/{target.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_holding_user_cannot_fetch_a_user_from_another_holding(env):
    app, _s, _ha, holding_b, _fa, _fb, _ar, _adr, _hr, target = env
    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_b.id)) as client:
        response = await client.get(f"/api/v1/users/{target.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_filial_admin_can_fetch_a_user_from_their_own_filial(env):
    app, _s, _ha, _hb, filial_a, _fb, _ar, _adr, _hr, target = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_a.id, role_slug="filial-admin")) as client:
        response = await client.get(f"/api/v1/users/{target.id}")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ordinary_filial_role_cannot_fetch_a_colleague_by_id(env):
    """Not filial-admin — GET /{id} isn't a dropdown use case, unlike list."""
    app, _s, _ha, _hb, filial_a, _fb, _ar, _adr, _hr, target = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_a.id, role_slug="asesor")) as client:
        response = await client.get(f"/api/v1/users/{target.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_platform_user_can_fetch_a_holding_scope_user(env):
    """Platform manages platform/holding-scope users directly; filial-scope
    users are onboarded by their own holding/filial-admin instead (same
    policy the create/update endpoints already enforced) — so this only
    covers a holding-scope target, not the filial-scope `target` fixture."""
    app, session, holding_a, _hb, _fa, _fb, _ar, _adr, holding_role, _target = env
    holding_admin = User(
        full_name="Holding Admin", email="admin@holding-a.com", status=UserStatus.ACTIVO,
        role_id=holding_role.id, holding_id=holding_a.id,
    )
    session.add(holding_admin)
    session.commit()

    async with _client_as(app, _user(RoleScope.PLATFORM)) as client:
        response = await client.get(f"/api/v1/users/{holding_admin.id}")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ordinary_filial_role_cannot_list_users(env):
    app, _s, _ha, _hb, filial_a, _fb, _ar, _adr, _hr, _target = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_a.id, role_slug="asesor")) as client:
        response = await client.get("/api/v1/users")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_filial_role_with_usuarios_accesos_permission_can_list_users(env):
    app, session, _ha, _hb, filial_a, _fb, asesor_role, _adr, _hr, _target = env
    session.add(RoleModulePermission(role_id=asesor_role.id, module_id="usuarios-accesos", access=AccessLevel.VER))
    session.commit()

    async with _client_as(
        app, _user(RoleScope.FILIAL, filial_id=filial_a.id, role_slug="asesor", role_id=asesor_role.id)
    ) as client:
        response = await client.get("/api/v1/users")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["email"] == "juan@filial-a1.com"


@pytest.mark.asyncio
async def test_ordinary_filial_role_can_still_use_the_user_directory(env):
    app, _s, _ha, _hb, filial_a, _fb, _ar, _adr, _hr, _target = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_a.id, role_slug="asesor")) as client:
        response = await client.get("/api/v1/users/directory")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["full_name"] == "Juan Pérez"
    assert "email" not in body[0]
    assert "permission_overrides" not in body[0]


@pytest.mark.asyncio
async def test_filial_admin_sees_full_user_data_in_the_list(env):
    app, _s, _ha, _hb, filial_a, _fb, _ar, _adr, _hr, _target = env
    async with _client_as(app, _user(RoleScope.FILIAL, filial_id=filial_a.id, role_slug="filial-admin")) as client:
        response = await client.get("/api/v1/users")

    body = response.json()
    assert body[0]["email"] == "juan@filial-a1.com"


@pytest.mark.asyncio
async def test_holding_user_sees_full_user_data_in_the_list(env):
    app, session, holding_a, _hb, _fa, _fb, _ar, _adr, holding_role, _target = env
    holding_admin = User(
        full_name="Holding Admin", email="admin@holding-a.com", status=UserStatus.ACTIVO,
        role_id=holding_role.id, holding_id=holding_a.id,
    )
    session.add(holding_admin)
    session.commit()

    async with _client_as(app, _user(RoleScope.HOLDING, holding_id=holding_a.id)) as client:
        response = await client.get("/api/v1/users")

    body = response.json()
    assert len(body) == 1
    assert body[0]["email"] == "admin@holding-a.com"
