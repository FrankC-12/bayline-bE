import uuid
from typing import Literal
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.auth.cookies import clear_session_cookies
from app.modules.auth.service import AuthService
from app.modules.auth.recovery import RecoveryService

router = APIRouter(prefix="/auth", tags=["Password recovery"])


class RecoveryEmail(BaseModel):
    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().casefold() if isinstance(value, str) else value


class RecoverySend(RecoveryEmail):
    channel: Literal["email", "sms"]


class OtpInput(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(pattern=r"^\d{6}$")


class ResetPassword(OtpInput):
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def password_bytes(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("La contraseña excede el máximo de 72 bytes.")
        return value


class PhoneInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")


class RecoveryChannel(BaseModel):
    channel: Literal["email", "sms"]
    destination: str
    available: bool


class RecoveryOptions(BaseModel):
    action: Literal["otp", "contact_admin", "unavailable"]
    message: str
    channels: list[RecoveryChannel]


class IssuedOtp(BaseModel):
    challenge_id: uuid.UUID
    destination: str
    expires_in: int


async def budget(db, request, identifier):
    ip = request.client.host if request.client else "unknown"
    await AuthService(db).check_login_budget(f"recovery:{identifier}", f"recovery:{ip}")


@router.post("/password-recovery/options", response_model=RecoveryOptions)
async def recovery_options(
    payload: RecoveryEmail, request: Request, db: AsyncSession = Depends(get_db)
):
    await budget(db, request, str(payload.email))
    return await RecoveryService(db).options(str(payload.email))


@router.post("/password-recovery/request", response_model=IssuedOtp)
async def recovery_request(
    payload: RecoverySend, request: Request, db: AsyncSession = Depends(get_db)
):
    await budget(db, request, str(payload.email))
    return await RecoveryService(db).request_reset(str(payload.email), payload.channel)


@router.post("/password-recovery/reset", status_code=204)
async def recovery_reset(
    payload: ResetPassword, request: Request, db: AsyncSession = Depends(get_db)
):
    await budget(db, request, str(payload.challenge_id))
    await RecoveryService(db).reset(payload.challenge_id, payload.code, payload.new_password)
    response = Response(status_code=204)
    clear_session_cookies(response)
    return response


@router.get("/recovery-phone")
async def phone_status(
    user: CurrentUser = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    return await RecoveryService(db).phone_status(user.user_id)


@router.post("/recovery-phone/request", response_model=IssuedOtp)
async def phone_request(
    payload: PhoneInput,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await budget(db, request, f"phone:{user.user_id}")
    return await RecoveryService(db).request_phone(
        user.user_id, payload.phone, payload.current_password
    )


@router.post("/recovery-phone/verify", status_code=204)
async def phone_verify(
    payload: OtpInput,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await budget(db, request, f"phone:{user.user_id}")
    await RecoveryService(db).verify_phone(user.user_id, payload.challenge_id, payload.code)
    return Response(status_code=204)
