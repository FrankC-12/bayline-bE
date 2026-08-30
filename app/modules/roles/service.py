import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.roles.enums import RoleScope
from app.modules.roles.exceptions import (
    InvalidModuleIdError,
    PermissionsNotAllowedForScopeError,
    RoleNotFoundError,
    RoleSlugAlreadyExistsError,
)
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.roles.module_catalog import MODULE_CATALOG
from app.modules.roles.schemas import ModulePermissionSchema, RoleCreate, RoleUpdate


class RoleService:
    """Business logic for managing roles and their module permissions."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_roles(self, scope: RoleScope | None = None) -> list[Role]:
        query = select(Role).options(selectinload(Role.permissions)).order_by(Role.name)
        if scope is not None:
            query = query.where(Role.scope == scope)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_role(self, role_id: uuid.UUID) -> Role:
        query = select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id)
        result = await self.db.execute(query)
        role = result.scalar_one_or_none()
        if role is None:
            raise RoleNotFoundError(str(role_id))
        return role

    async def create_role(self, payload: RoleCreate) -> Role:
        await self._ensure_slug_is_available(payload.slug)
        self._validate_permissions(payload.scope, payload.permissions)

        role = Role(
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            scope=payload.scope,
        )
        self.db.add(role)
        await self.db.flush()

        for perm in payload.permissions:
            self.db.add(
                RoleModulePermission(role_id=role.id, module_id=perm.module_id, access=perm.access)
            )

        await self.db.commit()
        return await self.get_role(role.id)

    async def update_role(self, role_id: uuid.UUID, payload: RoleUpdate) -> Role:
        role = await self.get_role(role_id)

        if payload.name:
            role.name = payload.name
        if payload.description is not None:
            role.description = payload.description

        if payload.permissions is not None:
            self._validate_permissions(role.scope, payload.permissions)
            for existing in list(role.permissions):
                await self.db.delete(existing)
            await self.db.flush()
            for perm in payload.permissions:
                self.db.add(
                    RoleModulePermission(role_id=role.id, module_id=perm.module_id, access=perm.access)
                )

        await self.db.commit()
        return await self.get_role(role.id)

    def _validate_permissions(
        self, scope: RoleScope, permissions: list[ModulePermissionSchema]
    ) -> None:
        if scope != RoleScope.FILIAL and permissions:
            raise PermissionsNotAllowedForScopeError(scope.value)

        for perm in permissions:
            if perm.module_id not in MODULE_CATALOG:
                raise InvalidModuleIdError(perm.module_id)

    async def _ensure_slug_is_available(self, slug: str) -> None:
        result = await self.db.execute(select(Role).where(Role.slug == slug))
        if result.scalar_one_or_none() is not None:
            raise RoleSlugAlreadyExistsError(slug)
