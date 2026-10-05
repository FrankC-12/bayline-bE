"""The técnico role (see seed_roles.py: "Acceso móvil a tareas asignadas e
inspecciones") is seeded with the `tecnico-servicio` module, not
asesor-servicios — but until now no endpoint actually checked
tecnico-servicio, so a técnico got a 403 trying to view even their own
assigned ODS. A técnico should be able to view (not edit) orders assigned
to them as the técnico, and list endpoints should silently scope down to
just their own orders instead of 403ing, while a técnico with no
assignment on a given order still gets rejected."""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders.router import _ensure_list_access, _ensure_order_access


@pytest.fixture
def make_user():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False, autoflush=False)
    filial_id = uuid.uuid4()

    def _make(module_id: str, access: AccessLevel):
        role = Role(id=uuid.uuid4(), name="Rol de prueba", slug=f"rol-{uuid.uuid4()}", scope=RoleScope.FILIAL)
        session.add(role)
        session.add(RoleModulePermission(role_id=role.id, module_id=module_id, access=access))
        session.commit()
        user = CurrentUser(
            user_id=uuid.uuid4(), email="user@test.com", role_id=role.id, role_slug=role.slug,
            scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
        )
        return AsyncAdapter(session), user, filial_id

    yield _make
    session.close()


def _order(filial_id, technician_user_id):
    return SimpleNamespace(filial_id=filial_id, technician_user_id=technician_user_id)


@pytest.mark.asyncio
async def test_tecnico_can_view_an_order_assigned_to_them(make_user):
    db, user, filial_id = make_user("tecnico-servicio", AccessLevel.EDITAR)
    order = _order(filial_id, technician_user_id=user.user_id)
    await _ensure_order_access(user, order, db)


@pytest.mark.asyncio
async def test_tecnico_cannot_view_an_order_assigned_to_someone_else(make_user):
    db, user, filial_id = make_user("tecnico-servicio", AccessLevel.EDITAR)
    order = _order(filial_id, technician_user_id=uuid.uuid4())
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_order_access(user, order, db)


@pytest.mark.asyncio
async def test_asesor_servicios_access_sees_any_order_regardless_of_assignment(make_user):
    db, user, filial_id = make_user("asesor-servicios", AccessLevel.VER)
    order = _order(filial_id, technician_user_id=uuid.uuid4())
    await _ensure_order_access(user, order, db)


@pytest.mark.asyncio
async def test_a_role_with_neither_module_is_rejected(make_user):
    db, user, filial_id = make_user("clientes-vehiculos", AccessLevel.EDITAR)
    order = _order(filial_id, technician_user_id=user.user_id)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_order_access(user, order, db)


@pytest.mark.asyncio
async def test_asesor_servicios_list_access_is_unrestricted(make_user):
    db, user, filial_id = make_user("asesor-servicios", AccessLevel.VER)
    assert await _ensure_list_access(user, filial_id, db) is None


@pytest.mark.asyncio
async def test_tecnico_list_access_is_scoped_to_their_own_user_id(make_user):
    db, user, filial_id = make_user("tecnico-servicio", AccessLevel.EDITAR)
    assert await _ensure_list_access(user, filial_id, db) == user.user_id


@pytest.mark.asyncio
async def test_a_role_with_neither_module_is_rejected_from_listing(make_user):
    db, user, filial_id = make_user("clientes-vehiculos", AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_list_access(user, filial_id, db)
