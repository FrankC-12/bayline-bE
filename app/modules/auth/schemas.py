import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.modules.roles.enums import RoleScope


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("password")
    @classmethod
    def password_byte_limit(cls, value):
        if len(value.encode("utf-8")) > 72:
            raise ValueError("La contraseña excede el máximo de 72 bytes.")
        return value


class RefreshRequest(BaseModel):
    """Only meaningful for a mobile client — the web app's refresh token
    never leaves the server as anything but an HttpOnly cookie, so this
    field is how a mobile client (which stored it itself after login)
    hands it back."""

    refresh_token: str | None = None


class LogoutRequest(RefreshRequest):
    device_id: uuid.UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class CurrentUser(BaseModel):
    """Claims decoded from the caller's JWT, representing who is making the request."""

    user_id: uuid.UUID
    email: str
    # Defaults to "" rather than being required — many tests construct
    # CurrentUser directly (bypassing login) without it, and the greeting
    # that needs this is the only consumer that cares if it's blank.
    full_name: str = ""
    role_id: uuid.UUID
    role_slug: str
    scope: RoleScope
    holding_id: uuid.UUID | None
    filial_id: uuid.UUID | None


class AccessMapResponse(BaseModel):
    modules: dict[str, str]
