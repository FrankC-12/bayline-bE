"""A single save must return the permission set that was actually committed."""

import uuid

import pytest

from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.roles.schemas import ModulePermissionSchema, RoleRead, RoleUpdate
from app.modules.roles.service import RoleService
from app.modules.users.models import User, UserModulePermission
from app.modules.users.schemas import UserRead, UserUpdate
from app.modules.users.service import UserService


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["role", "user"])
async def test_first_save_returns_new_permissions_for_grants_revocations_and_reset(
    billing_db, target
):
    ctx = billing_db
    async with ctx.sessions() as db:
        role = Role(name="Permisos QA", slug=f"permissions-{uuid.uuid4()}", scope=RoleScope.FILIAL)
        db.add(role)
        await db.flush()
        db.add(RoleModulePermission(role_id=role.id, module_id="almacen", access=AccessLevel.VER))
        user = User(
            full_name="Permisos QA",
            email=f"{uuid.uuid4()}@example.com",
            role_id=role.id,
            filial_id=ctx.filial_id,
        )
        db.add(user)
        await db.flush()
        db.add(UserModulePermission(user_id=user.id, module_id="almacen", access=AccessLevel.VER))
        await db.commit()
        role_id, user_id = role.id, user.id
        cases = [
            [
                ModulePermissionSchema(module_id="almacen", access=AccessLevel.EDITAR),
                ModulePermissionSchema(module_id="clientes-vehiculos", access=AccessLevel.VER),
            ],
            [ModulePermissionSchema(module_id="almacen", access=AccessLevel.VER)],
            [],
        ]
        for permissions in cases:
            if target == "role":
                result = await RoleService(db).update_role(
                    role_id, RoleUpdate(permissions=permissions)
                )
                returned = RoleRead.model_validate(result).permissions
            else:
                result = await UserService(db).update_user(
                    user_id, UserUpdate(permission_overrides=permissions)
                )
                returned = UserRead.model_validate(result).permission_overrides
            expected = {item.module_id: item.access for item in permissions}
            assert {item.module_id: item.access for item in returned} == expected
            async with ctx.sessions() as reader:
                if target == "role":
                    saved = (await RoleService(reader).get_role(role_id)).permissions
                else:
                    saved = (await UserService(reader).get_user(user_id)).permission_overrides
                assert {item.module_id: item.access for item in saved} == expected
