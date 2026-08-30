import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HoldingBase(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    slug: str = Field(min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class HoldingCreate(HoldingBase):
    pass


class HoldingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    slug: str | None = Field(default=None, min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class HoldingRead(HoldingBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
