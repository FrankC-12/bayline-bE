import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The fixed set of vehicle body types — matches the choices the frontend
# used to offer as a free-standing dropdown at vehicle intake. Now chosen
# once per Model here, so a vehicle inherits it instead of it being picked
# by hand every time (see app/modules/clients).
VehicleBodyType = Literal["Sedán", "Pick-up", "SUV", "Camión", "Van", "Moto", "Otro"]


class VehicleModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    vehicle_type: VehicleBodyType


class VehicleModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    vehicle_type: VehicleBodyType | None = None


class VehicleModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand_id: uuid.UUID
    name: str
    vehicle_type: str | None
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
