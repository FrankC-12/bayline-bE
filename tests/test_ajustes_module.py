"""Ajustes (IVA, IGTF, tasa BCV, comisión, mano de obra) now has its own
module id, independent of post-ventas — a role with post-ventas access
does NOT automatically get ajustes access, and vice versa."""

import os
import uuid

os.environ["DEBUG"] = "false"

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
from app.modules.roles.module_catalog import MODULE_CATALOG
from app.modules.roles.permissions import ensure_module_access


def test_ajustes_is_a_registered_module_id():
    assert "ajustes" in MODULE_CATALOG


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        role = Role(id=uuid.uuid4(), name="Administrador de prueba", slug="admin-test", scope=RoleScope.FILIAL)
        session.add(role)
        # Only post-ventas is granted — ajustes is deliberately left unset.
        session.add(RoleModulePermission(role_id=role.id, module_id="post-ventas", access=AccessLevel.EDITAR))
        session.commit()
        user = CurrentUser(
            user_id=uuid.uuid4(), email="admin@test.com", role_id=role.id, role_slug=role.slug,
            scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
        )
        yield AsyncAdapter(session), user, filial_id


@pytest.mark.asyncio
async def test_post_ventas_access_does_not_grant_ajustes(env):
    db, user, filial_id = env

    await ensure_module_access(db, user, filial_id, "post-ventas", AccessLevel.EDITAR)

    with pytest.raises(InsufficientPermissionsError):
        await ensure_module_access(db, user, filial_id, "ajustes", AccessLevel.VER)


@pytest.mark.asyncio
async def test_granting_ajustes_does_not_affect_post_ventas(env):
    db, user, filial_id = env
    db.session.add(RoleModulePermission(role_id=user.role_id, module_id="ajustes", access=AccessLevel.VER))
    db.session.commit()

    await ensure_module_access(db, user, filial_id, "ajustes", AccessLevel.VER)
    with pytest.raises(InsufficientPermissionsError):
        await ensure_module_access(db, user, filial_id, "ajustes", AccessLevel.EDITAR)
    await ensure_module_access(db, user, filial_id, "post-ventas", AccessLevel.EDITAR)
