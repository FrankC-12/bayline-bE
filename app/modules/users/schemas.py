import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.roles.schemas import ModulePermissionSchema
from app.modules.users.enums import UserStatus


class UserCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    email: EmailStr
    role_id: uuid.UUID
    holding_id: uuid.UUID | None = None
    filial_id: uuid.UUID | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    permission_overrides: list[ModulePermissionSchema] = Field(default_factory=list)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    role_id: uuid.UUID | None = None
    holding_id: uuid.UUID | None = None
    filial_id: uuid.UUID | None = None
    status: UserStatus | None = None
    permission_overrides: list[ModulePermissionSchema] | None = None


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    status: UserStatus
    role_id: uuid.UUID
    holding_id: uuid.UUID | None
    filial_id: uuid.UUID | None
    permission_overrides: list[ModulePermissionSchema]
    created_at: datetime
    updated_at: datetime
