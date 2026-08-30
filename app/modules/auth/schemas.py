import uuid

from pydantic import BaseModel, EmailStr

from app.modules.roles.enums import RoleScope


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CurrentUser(BaseModel):
    """Claims decoded from the caller's JWT, representing who is making the request."""

    user_id: uuid.UUID
    email: str
    role_id: uuid.UUID
    role_slug: str
    scope: RoleScope
    holding_id: uuid.UUID | None
    filial_id: uuid.UUID | None
