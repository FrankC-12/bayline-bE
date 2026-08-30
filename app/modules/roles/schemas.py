import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.roles.enums import AccessLevel, RoleScope


class ModulePermissionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    module_id: str
    access: AccessLevel


class RoleBase(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    description: str | None = Field(default=None, max_length=255)
    scope: RoleScope


class RoleCreate(RoleBase):
    permissions: list[ModulePermissionSchema] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[ModulePermissionSchema] | None = None


class RoleRead(RoleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    permissions: list[ModulePermissionSchema]
    created_at: datetime
    updated_at: datetime
