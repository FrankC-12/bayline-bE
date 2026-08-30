import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.parts.enums import PartAvailability, PartSaleStatus, ReturnCondition, ReturnReason


class PartCreate(BaseModel):
    filial_id: uuid.UUID
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=150)
    price: float = Field(ge=0)
    stock_quantity: int = Field(default=0, ge=0)
    min_stock: int = Field(default=10, ge=0)


class PartUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    price: float | None = Field(default=None, ge=0)
    stock_quantity: int | None = Field(default=None, ge=0)
    min_stock: int | None = Field(default=None, ge=0)


class PartRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    name: str
    price: float
    stock_quantity: int
    min_stock: int
    availability: PartAvailability
    created_at: datetime
    updated_at: datetime


class PartBulkItem(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=150)
    price: float = Field(ge=0)
    stock_quantity: int = Field(default=0, ge=0)
    min_stock: int = Field(default=10, ge=0)


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
    request_reason: str = Field(default="Venta de Repuestos", max_length=150)
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