import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FilialBase(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    slug: str = Field(min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class FilialCreate(FilialBase):
    holding_id: uuid.UUID


class FilialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    slug: str | None = Field(default=None, min_length=2, max_length=150, pattern=r"^[a-z0-9-]+$")


class FilialRead(FilialBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    holding_id: uuid.UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
