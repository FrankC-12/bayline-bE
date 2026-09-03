from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import AccessMapResponse, CurrentUser, LoginRequest, RefreshRequest, TokenResponse
from app.modules.roles.models import RoleModulePermission
from app.modules.roles.module_catalog import MODULE_CATALOG
from app.modules.users.models import UserModulePermission
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["Auth"])


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(db)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Authenticate with email and password, returning a Bearer access token."""
    return await service.login(payload.email, payload.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Rotate a valid refresh token and return a new token pair."""
    return await service.refresh(payload.refresh_token)


@router.get("/me", response_model=CurrentUser)
async def get_me(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Return the claims of the currently authenticated caller."""
    return current_user


@router.get("/access", response_model=AccessMapResponse)
async def get_access_map(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AccessMapResponse:
    if current_user.role_slug == "filial-admin":
        return AccessMapResponse(modules={module: "editar" for module in MODULE_CATALOG})
    role_rows = await db.execute(
        select(RoleModulePermission).where(RoleModulePermission.role_id == current_user.role_id)
    )
    modules = {row.module_id: row.access.value for row in role_rows.scalars().all()}
    override_rows = await db.execute(
        select(UserModulePermission).where(UserModulePermission.user_id == current_user.user_id)
    )
    modules.update({row.module_id: row.access.value for row in override_rows.scalars().all()})
    return AccessMapResponse(modules=modules)
