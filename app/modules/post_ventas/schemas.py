import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.post_ventas.enums import TemparioCategory


class LaborSettingsUpdate(BaseModel):
    hourly_rate: float = Field(ge=0)
    commission_percentage: float = Field(ge=0, le=100)
    igtf_percentage: float = Field(ge=0, le=100)
    iva_percentage: float = Field(ge=0, le=100)
    bcv_rate: float = Field(ge=0)


class LaborSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    filial_id: uuid.UUID
    hourly_rate: float
    commission_percentage: float
    igtf_percentage: float
    iva_percentage: float
    bcv_rate: float
    updated_at: datetime


class CompatibleVehicle(BaseModel):
    brand: str
    model: str


class TemparioPartInput(BaseModel):
    part_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=150)
    quantity: int = Field(ge=1, default=1)
    unit_cost: float = Field(ge=0, default=0)


class TemparioPartRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID | None
    name: str
    quantity: int
    unit_cost: float
    subtotal: float


class TemparioCreate(BaseModel):
    filial_id: uuid.UUID
    category: TemparioCategory
    sequence_number: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=2, max_length=150)
    estimated_hours: float = Field(ge=0)
    year_from: int | None = None
    year_to: int | None = None
    compatible_vehicles: list[CompatibleVehicle] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    requires_parts: bool = True
    parts: list[TemparioPartInput] = Field(default_factory=list)


class TemparioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    estimated_hours: float | None = Field(default=None, ge=0)
    year_from: int | None = None
    year_to: int | None = None
    compatible_vehicles: list[CompatibleVehicle] | None = None
    tools: list[str] | None = None
    requires_parts: bool | None = None
    parts: list[TemparioPartInput] | None = None


class TemparioRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    category: TemparioCategory
    name: str
    estimated_hours: float
    year_from: int | None
    year_to: int | None
    compatible_vehicles: list[CompatibleVehicle]
    tools: list[str]
    requires_parts: bool
    parts: list[TemparioPartRead]
    parts_cost: float
    parts_margin: float
    labor_cost: float
    total_price: float
    created_at: datetime
    updated_at: datetime