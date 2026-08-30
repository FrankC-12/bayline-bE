import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.service_orders.enums import (
    ServiceOrderStatus,
    ServiceOrderType,
    TaskStatus,
    TransferStatus,
    UpsellStatus,
)


class BayCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class BayUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    is_active: bool | None = None


class BayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    name: str
    is_active: bool


class ServiceOrderCreate(BaseModel):
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    order_type: ServiceOrderType = ServiceOrderType.REGULAR
    notes: str | None = None
    # Used by "Agendar Orden de Servicio" in the Calendario view — all optional
    # so a normal walk-in ODS (created from the kanban) can omit them.
    scheduled_at: datetime | None = None
    technician_user_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = None


class ServiceOrderUpdate(BaseModel):
    status: ServiceOrderStatus | None = None
    order_type: ServiceOrderType | None = None
    technician_user_id: uuid.UUID | None = None
    advisor_user_id: uuid.UUID | None = None
    bay_id: uuid.UUID | None = None
    scheduled_at: datetime | None = None
    notes: str | None = None
    # "None" above means "leave unchanged" — these flags are how the client
    # explicitly asks to clear a nullable assignment back to "Sin asignar".
    clear_technician: bool = False
    clear_advisor: bool = False
    clear_bay: bool = False


class ServiceOrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    vehicle_id: uuid.UUID
    status: ServiceOrderStatus
    order_type: ServiceOrderType
    technician_user_id: uuid.UUID | None
    advisor_user_id: uuid.UUID | None
    bay_id: uuid.UUID | None
    notes: str | None
    scheduled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    total_amount: float | None


class TaskCreate(BaseModel):
    tempario_id: uuid.UUID


class TaskStatusUpdate(BaseModel):
    status: TaskStatus


class TaskRead(BaseModel):
    id: uuid.UUID
    tempario_id: uuid.UUID
    code_snapshot: str
    name_snapshot: str
    hours_snapshot: float
    status: TaskStatus
    created_at: datetime


class TransferLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)


class TransferLineRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_price: float
    subtotal: float


class TransferRead(BaseModel):
    id: uuid.UUID
    code: str
    status: TransferStatus
    lines: list[TransferLineRead]
    subtotal: float
    fulfilled_by_user_id: uuid.UUID | None = None
    fulfilled_at: datetime | None = None
    created_at: datetime


class OrderSummary(BaseModel):
    tasks: list[TaskRead]
    transfers: list[TransferRead]
    parts_subtotal: float
    labor_subtotal: float
    iva_percentage: float
    iva_amount: float
    total: float


class UpsellCreate(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=2)
    evidence_count: int = Field(default=0, ge=0)
    detected_by_user_id: uuid.UUID | None = None


class UpsellStatusUpdate(BaseModel):
    status: UpsellStatus


class UpsellRead(BaseModel):
    id: uuid.UUID
    service_order_id: uuid.UUID
    title: str
    description: str
    detected_by_user_id: uuid.UUID | None
    evidence_count: int
    status: UpsellStatus
    created_at: datetime
    resolved_at: datetime | None