import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.inspections.enums import InspectionStatus


class InspectionDamageInput(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    zone: str = Field(min_length=1, max_length=60)
    kind: str = Field(min_length=1, max_length=60)
    severity: str = Field(min_length=1, max_length=30)
    description: str | None = Field(default=None, max_length=300)


class InspectionDamageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    x: float
    y: float
    zone: str
    kind: str
    severity: str
    description: str | None
    photo_url: str | None


class InspectionCreate(BaseModel):
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    mileage: int | None = Field(default=None, ge=0)
    notes: str | None = None
    status: InspectionStatus = InspectionStatus.COMPLETADA
    damages: list[InspectionDamageInput] = Field(default_factory=list)


class InspectionUpdate(BaseModel):
    mileage: int | None = None
    notes: str | None = None
    status: InspectionStatus | None = None
    service_order_id: uuid.UUID | None = None
    clear_service_order: bool = False


class InspectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    inspector_user_id: uuid.UUID
    service_order_id: uuid.UUID | None
    mileage: int | None
    notes: str | None
    status: InspectionStatus
    damages: list[InspectionDamageRead]
    photo_urls: list[str]
    created_at: datetime
    updated_at: datetime