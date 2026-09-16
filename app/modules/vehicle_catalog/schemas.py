import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VehicleModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class VehicleModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)


class VehicleModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    is_active: bool


class VehicleBrandCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class VehicleBrandUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)


class VehicleBrandRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    holding_id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime
    models: list[VehicleModelRead] = Field(default_factory=list)
