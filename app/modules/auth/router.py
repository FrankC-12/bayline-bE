from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import DomainError
from app.modules.auth.cookies import REFRESH_COOKIE, clear_session_cookies, set_session_cookies
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import AccessMapResponse, CurrentUser, LoginRequest
from app.modules.auth.service import AuthService
from app.modules.roles.models import RoleModulePermission
from app.modules.roles.module_catalog import MODULE_CATALOG
from app.modules.users.models import UserModulePermission

router = APIRouter(prefix="/auth", tags=["Auth"])


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(db)


@router.post("/login")
async def login(
    payload: LoginRequest,
    response: Response,
    service: AuthService = Depends(get_auth_service),
):
    tokens = await service.login(payload.email, payload.password)
    set_session_cookies(response, tokens)
    return {"authenticated": True}


@router.post("/refresh")
async def refresh(request: Request, service: AuthService = Depends(get_auth_service)):
    try:
        tokens = await service.refresh(request.cookies.get(REFRESH_COOKIE, ""))
    except DomainError as exc:
        response = JSONResponse(
            {"message": exc.message, "errorCode": exc.error_code}, status_code=exc.status_code
        )
        clear_session_cookies(response)
        return response
    response = JSONResponse({"authenticated": True})
    set_session_cookies(response, tokens)
    return response


@router.post("/logout", status_code=204)
async def logout():
    response = Response(status_code=204)
    clear_session_cookies(response)
    return response


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
