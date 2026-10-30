import uuid

from sqlalchemy import select, update
from datetime import UTC, datetime
from app.modules.auth.models import RefreshSession
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.modules.filiales.service import FilialService
from app.modules.holdings.service import HoldingService
from app.modules.roles.enums import RoleScope
from app.modules.roles.module_catalog import MODULE_CATALOG
from app.modules.roles.service import RoleService
from app.modules.users.enums import UserStatus
from app.modules.users.exceptions import (
    EmailAlreadyExistsError,
    FilialHoldingMismatchError,
    FilialRequiredForScopeError,
    HoldingRequiredForScopeError,
    InvalidModuleIdError,
    PermissionOverridesNotAllowedError,
    ScopeDoesNotAllowTenantError,
    UserNotFoundError,
)
from app.modules.users.models import User, UserModulePermission
from app.modules.users.schemas import ModulePermissionSchema, UserCreate, UserUpdate


class UserService:
    """Business logic for creating and managing users across the tenant hierarchy."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.role_service = RoleService(db)
        self.holding_service = HoldingService(db)
        self.filial_service = FilialService(db)

    async def list_users(
        self,
        holding_id: uuid.UUID | None = None,
        filial_id: uuid.UUID | None = None,
        *,
        active_only: bool = False,
    ) -> list[User]:
        query = (
            select(User)
            .options(selectinload(User.permission_overrides))
            .order_by(User.created_at.desc())
        )
        if active_only:
            query = query.where(User.status == UserStatus.ACTIVO)
        if holding_id is not None:
            query = query.where(User.holding_id == holding_id)
        if filial_id is not None:
            query = query.where(User.filial_id == filial_id)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_user(self, user_id: uuid.UUID) -> User:
        query = (
            select(User)
            .options(selectinload(User.permission_overrides))
            .where(User.id == user_id)
            # Replacing override rows does not refresh an already-loaded
            # relationship in the identity map. Return the committed values.
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()
        if user is None:
            raise UserNotFoundError(str(user_id))
        return user

    async def create_user(self, payload: UserCreate) -> User:
        role = await self.role_service.get_role(payload.role_id)
        holding_id, filial_id = await self._resolve_tenant(
            role.scope, payload.holding_id, payload.filial_id
        )
        self._validate_overrides(role.scope, payload.permission_overrides)
        await self._ensure_email_is_available(payload.email)

        user = User(
            full_name=payload.full_name,
            email=payload.email,
            role_id=role.id,
            holding_id=holding_id,
            filial_id=filial_id,
            password_hash=hash_password(payload.password) if payload.password else None,
            status=UserStatus.ACTIVO if payload.password else UserStatus.INVITADO,
        )
        self.db.add(user)
        await self.db.flush()

        for perm in payload.permission_overrides:
            self.db.add(
                UserModulePermission(user_id=user.id, module_id=perm.module_id, access=perm.access)
            )

        await self.db.commit()
        return await self.get_user(user.id)

    async def update_user(self, user_id: uuid.UUID, payload: UserUpdate) -> User:
        # Serialize changes to the same account so every suspension advances
        # its access-token generation and revokes refresh sessions atomically.
        await self.db.execute(select(User.id).where(User.id == user_id).with_for_update())
        user = await self.get_user(user_id)
        role = await self.role_service.get_role(payload.role_id or user.role_id)

        tenant_touched = (
            payload.role_id is not None
            or payload.holding_id is not None
            or payload.filial_id is not None
        )
        if tenant_touched:
            holding_id, filial_id = await self._resolve_tenant(
                role.scope,
                payload.holding_id if payload.holding_id is not None else user.holding_id,
                payload.filial_id if payload.filial_id is not None else user.filial_id,
            )
            user.role_id = role.id
            user.holding_id = holding_id
            user.filial_id = filial_id

        if payload.full_name:
            user.full_name = payload.full_name
        if payload.status:
            if payload.status != UserStatus.ACTIVO and user.status != payload.status:
                from app.modules.auth.models import RecoveryChallenge

                await self.db.execute(
                    update(RecoveryChallenge)
                    .where(
                        RecoveryChallenge.user_id == user.id,
                        RecoveryChallenge.consumed_at.is_(None),
                    )
                    .values(consumed_at=datetime.now(UTC))
                )
                user.session_version += 1
                await self.db.execute(
                    update(RefreshSession)
                    .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
                    .values(revoked_at=datetime.now(UTC))
                )
            user.status = payload.status

        if payload.permission_overrides is not None:
            self._validate_overrides(role.scope, payload.permission_overrides)
            for existing in list(user.permission_overrides):
                await self.db.delete(existing)
            await self.db.flush()
            for perm in payload.permission_overrides:
                self.db.add(
                    UserModulePermission(
                        user_id=user.id, module_id=perm.module_id, access=perm.access
                    )
                )

        await self.db.commit()
        return await self.get_user(user.id)

    async def reset_password(self, user_id, actor_id, current_password, new_password):
        from app.core.security import verify_password
        from app.core.exceptions import BadRequestError
        from app.modules.auth.organization_access import ensure_account_available, revoke_accounts

        accounts = (
            (
                await self.db.execute(
                    select(User)
                    .where(User.id.in_([user_id, actor_id]))
                    .order_by(User.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        by_id = {account.id: account for account in accounts}
        actor, target = by_id.get(actor_id), by_id.get(user_id)
        if target is None:
            raise UserNotFoundError(str(user_id))
        if (
            actor is None
            or not actor.password_hash
            or not verify_password(current_password, actor.password_hash)
        ):
            raise BadRequestError(
                "La contraseña del administrador es incorrecta.",
                error_code="invalid_current_password",
            )
        await ensure_account_available(self.db, actor)
        from app.core.exceptions import DomainError

        try:
            await ensure_account_available(self.db, target)
        except DomainError:
            raise BadRequestError(
                "No se puede restablecer la contraseña de una cuenta u organización suspendida.",
                error_code="account_unavailable",
            ) from None
        target.password_hash = hash_password(new_password)
        await self.db.flush()
        await revoke_accounts(self.db, select(User.id).where(User.id == user_id))
        await self.db.commit()

    async def _resolve_tenant(
        self, scope: RoleScope, holding_id: uuid.UUID | None, filial_id: uuid.UUID | None
    ) -> tuple[uuid.UUID | None, uuid.UUID | None]:
        if scope == RoleScope.PLATFORM:
            if holding_id or filial_id:
                raise ScopeDoesNotAllowTenantError()
            return None, None

        if scope == RoleScope.HOLDING:
            if not holding_id:
                raise HoldingRequiredForScopeError()
            await self.holding_service.get_holding(holding_id)
            return holding_id, None

        # scope == RoleScope.FILIAL
        if not filial_id:
            raise FilialRequiredForScopeError()
        filial = await self.filial_service.get_filial(filial_id)
        if holding_id and holding_id != filial.holding_id:
            raise FilialHoldingMismatchError()
        return filial.holding_id, filial_id

    def _validate_overrides(
        self, scope: RoleScope, overrides: list[ModulePermissionSchema]
    ) -> None:
        if scope != RoleScope.FILIAL and overrides:
            raise PermissionOverridesNotAllowedError()
        for perm in overrides:
            if perm.module_id not in MODULE_CATALOG:
                raise InvalidModuleIdError(perm.module_id)

    async def _ensure_email_is_available(self, email: str) -> None:
        result = await self.db.execute(select(User).where(User.email == email))
        if result.scalar_one_or_none() is not None:
            raise EmailAlreadyExistsError(email)
