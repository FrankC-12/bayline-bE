import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.warehouse.enums import MovementReason, MovementType, TransferStatus


class WarehouseCreate(BaseModel):
    filial_id: uuid.UUID
    name: str = Field(min_length=1, max_length=80)


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    is_active: bool | None = None


class WarehouseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    name: str
    is_active: bool
    created_at: datetime


class LotLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)
    unit_cost: float = Field(ge=0)
    location: str | None = None


class StockInCreate(BaseModel):
    filial_id: uuid.UUID
    warehouse_id: uuid.UUID
    reason: str = Field(default="Compra directa", max_length=80)
    lines: list[LotLineInput] = Field(min_length=1)


class PartLotRead(BaseModel):
    id: uuid.UUID
    code: str
    warehouse_id: uuid.UUID
    part_id: uuid.UUID
    quantity_received: int
    quantity_remaining: int
    unit_cost: float
    location: str | None
    note: str | None
    received_at: datetime


class BulkLotItem(BaseModel):
    part_code: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=1)
    unit_cost: float = Field(ge=0)
    location: str | None = None


class BulkLotCreate(BaseModel):
    filial_id: uuid.UUID
    warehouse_id: uuid.UUID
    items: list[BulkLotItem] = Field(min_length=1, max_length=500)


class BulkLotResult(BaseModel):
    created: list[PartLotRead]
    skipped: list[str]


class StockOutCreate(BaseModel):
    filial_id: uuid.UUID
    warehouse_id: uuid.UUID
    part_id: uuid.UUID
    quantity: int = Field(ge=1)
    reason: MovementReason = MovementReason.CONSUMO_ODS
    reference: str | None = Field(default=None, max_length=80)


class TransferLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)


class TransferCreate(BaseModel):
    filial_id: uuid.UUID
    origin_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    note: str | None = None
    lines: list[TransferLineInput] = Field(min_length=1)


class TransferStatusUpdate(BaseModel):
    status: TransferStatus


class TransferLineRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_cost: float
    subtotal: float


class TransferRead(BaseModel):
    id: uuid.UUID
    code: str
    origin_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    status: TransferStatus
    note: str | None
    lines: list[TransferLineRead]
    total_cost: float
    created_at: datetime
    completed_at: datetime | None
    completed_by_user_id: uuid.UUID | None


class StockMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    warehouse_id: uuid.UUID
    part_id: uuid.UUID
    movement_type: MovementType
    quantity: int
    unit_cost: float | None
    reference: str | None
    note: str | None
    responsible_user_id: uuid.UUID | None
    created_at: datetime


class InventoryRow(BaseModel):
    part_id: uuid.UUID
    part_code: str
    part_name: str
    warehouse_id: uuid.UUID
    warehouse_name: str
    quantity: int
    average_cost: float | None
    location: str | None
    min_stock: int