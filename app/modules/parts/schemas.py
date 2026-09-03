import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.parts.enums import PartSaleStatus, ReturnCondition, ReturnReason


class PartCreate(BaseModel):
    filial_id: uuid.UUID
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=150)
    category: str = Field(min_length=1, max_length=80)
    brand: str = Field(min_length=1, max_length=80)
    application: str = Field(min_length=1, max_length=180)
    unit: str = Field(min_length=1, max_length=30)


class PartUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=150)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    brand: str | None = Field(default=None, min_length=1, max_length=80)
    application: str | None = Field(default=None, min_length=1, max_length=180)
    unit: str | None = Field(default=None, min_length=1, max_length=30)


class PartRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    name: str
    category: str
    brand: str
    application: str
    unit: str
    stock_total: int = 0
    reference_price: float | None = None
    created_at: datetime
    updated_at: datetime


class PartBulkItem(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=150)
    category: str = Field(min_length=1, max_length=80)
    brand: str = Field(min_length=1, max_length=80)
    application: str = Field(min_length=1, max_length=180)
    unit: str = Field(min_length=1, max_length=30)


class PartBulkCreate(BaseModel):
    filial_id: uuid.UUID
    items: list[PartBulkItem] = Field(min_length=1, max_length=500)


class PartBulkResult(BaseModel):
    created: list[PartRead]
    skipped: list[str]


class PartSaleLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)


class PartSaleLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_price: float
    unit_cost: float | None


class PartSaleCreate(BaseModel):
    filial_id: uuid.UUID
    client_name: str = Field(min_length=2, max_length=150)
    client_document: str | None = None
    discount_label: str = Field(default="Costo + 30% (Sin Descuento)", max_length=60)
    lines: list[PartSaleLineInput] = Field(min_length=1)


class PartSaleUpdate(BaseModel):
    status: PartSaleStatus | None = None


class PartSaleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    client_name: str
    client_document: str | None
    request_reason: str
    discount_label: str
    status: PartSaleStatus
    total: float
    lines: list[PartSaleLineRead]
    created_at: datetime
    updated_at: datetime


class PartReturnCreate(BaseModel):
    filial_id: uuid.UUID
    part_id: uuid.UUID
    condition: ReturnCondition = ReturnCondition.NUEVO
    origin_warehouse: str = Field(min_length=1, max_length=60)
    destination_warehouse: str = Field(min_length=1, max_length=60)
    quantity: int = Field(ge=1)
    reason: ReturnReason
    reason_notes: str | None = None


class PartReturnRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    part_id: uuid.UUID
    condition: ReturnCondition
    origin_warehouse: str
    destination_warehouse: str
    quantity: int
    reason: ReturnReason
    reason_notes: str | None
    responsible_user_id: uuid.UUID
    created_at: datetime
