"""Cobrar, egreso, reversar and ver-rentabilidad used to ride along with a
broader module (asesor-servicios / movimientos-manuales / administracion) —
each is now its own fine-grained module_id, independently grantable, since
Finanzas actions are sensitive enough that e.g. creating an egreso shouldn't
imply being able to reverse one."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.router import (
    _ensure_egreso_access,
    _ensure_manual_movement_access,
    _ensure_rentabilidad_access,
    _ensure_reversar_access,
)
from app.modules.auth.exceptions import InsufficientPermissionsError
from app.modules.auth.schemas import CurrentUser
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders.router import _ensure_cobrar_access


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


@pytest.mark.asyncio
async def test_movimientos_manuales_alone_no_longer_grants_egreso_or_reversar(make_user):
    db, user, filial_id = make_user("movimientos-manuales", AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_egreso_access(user, filial_id, db, AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_reversar_access(user, filial_id, db, AccessLevel.EDITAR)
    # It still grants what it always did (ingreso manual), untouched by the split.
    await _ensure_manual_movement_access(user, filial_id, db, AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_finanzas_egreso_grants_egreso_but_not_reversar(make_user):
    db, user, filial_id = make_user("finanzas-egreso", AccessLevel.EDITAR)
    await _ensure_egreso_access(user, filial_id, db, AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_reversar_access(user, filial_id, db, AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_finanzas_reversar_is_independent_of_egreso(make_user):
    db, user, filial_id = make_user("finanzas-reversar", AccessLevel.EDITAR)
    await _ensure_reversar_access(user, filial_id, db, AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_egreso_access(user, filial_id, db, AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_asesor_servicios_no_longer_grants_cobrar(make_user):
    db, user, filial_id = make_user("asesor-servicios", AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_cobrar_access(user, filial_id, db, AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_finanzas_cobrar_grants_cobrar(make_user):
    db, user, filial_id = make_user("finanzas-cobrar", AccessLevel.EDITAR)
    await _ensure_cobrar_access(user, filial_id, db, AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_administracion_no_longer_grants_rentabilidad(make_user):
    db, user, filial_id = make_user("administracion", AccessLevel.EDITAR)
    with pytest.raises(InsufficientPermissionsError):
        await _ensure_rentabilidad_access(user, filial_id, db, AccessLevel.VER)


@pytest.mark.asyncio
async def test_finanzas_rentabilidad_grants_rentabilidad(make_user):
    db, user, filial_id = make_user("finanzas-rentabilidad", AccessLevel.VER)
    await _ensure_rentabilidad_access(user, filial_id, db, AccessLevel.VER)
