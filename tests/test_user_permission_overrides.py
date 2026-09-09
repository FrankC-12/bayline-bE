"""A per-user permission override can now REVOKE access below the role's
default (access="sin_acceso"), not just grant above it — while a role
itself is still never allowed to declare "sin_acceso" (row absence stays
the only way to represent "no access" at the role level)."""

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
from app.modules.roles.exceptions import InvalidAccessLevelForRoleError
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.roles.permissions import ensure_module_access
from app.modules.roles.schemas import ModulePermissionSchema
from app.modules.roles.service import RoleService
from app.modules.users.models import UserModulePermission


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        role = Role(id=uuid.uuid4(), name="Asesor de prueba", slug="asesor-test", scope=RoleScope.FILIAL)
        session.add(role)
        session.add(RoleModulePermission(role_id=role.id, module_id="administracion", access=AccessLevel.EDITAR))
        session.commit()
        user = CurrentUser(
            user_id=uuid.uuid4(), email="asesor@test.com", role_id=role.id, role_slug=role.slug,
            scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
        )
        yield AsyncAdapter(session), session, user, filial_id


@pytest.mark.asyncio
async def test_sin_acceso_override_revokes_access_the_role_grants(env):
    db, session, user, filial_id = env
    session.add(
        UserModulePermission(user_id=user.user_id, module_id="administracion", access=AccessLevel.SIN_ACCESO)
    )
    session.commit()

    with pytest.raises(InsufficientPermissionsError):
        await ensure_module_access(db, user, filial_id, "administracion", AccessLevel.VER)
    with pytest.raises(InsufficientPermissionsError):
        await ensure_module_access(db, user, filial_id, "administracion", AccessLevel.EDITAR)


@pytest.mark.asyncio
async def test_without_override_the_role_grant_still_applies(env):
    db, _session, user, filial_id = env
    await ensure_module_access(db, user, filial_id, "administracion", AccessLevel.EDITAR)


def test_role_permission_cannot_declare_sin_acceso():
    service = RoleService(db=None)  # _validate_permissions never touches self.db
    with pytest.raises(InvalidAccessLevelForRoleError):
        service._validate_permissions(
            RoleScope.FILIAL,
            [ModulePermissionSchema(module_id="administracion", access=AccessLevel.SIN_ACCESO)],
        )
