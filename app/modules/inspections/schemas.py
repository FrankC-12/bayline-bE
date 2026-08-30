import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.inspections.enums import InspectionStatus


class InspectionCreate(BaseModel):
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    mileage: int | None = Field(default=None, ge=0)
    notes: str | None = None
    status: InspectionStatus = InspectionStatus.COMPLETADA


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
    created_at: datetime
    updated_at: datetime