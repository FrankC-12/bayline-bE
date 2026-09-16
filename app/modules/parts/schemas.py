import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.parts.enums import PartSaleStatus, ReturnCondition, ReturnReason
from app.modules.parts.pricing import DEFAULT_DISCOUNT, DiscountLabel


class PartCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class PartCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)


class PartCategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    holding_id: uuid.UUID
    name: str
    is_active: bool


class PartMeasureCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class PartMeasureUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)


class PartMeasureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    holding_id: uuid.UUID
    name: str
    is_active: bool


class PartCreate(BaseModel):
    filial_id: uuid.UUID
    code: str = Field(min_length=1, max_length=40)
    manufacturer_part_number: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=150)
    category_id: uuid.UUID
    # Vehicle fit — both optional (a truly generic part has neither); years
    # independently optional, empty = fits every year (universal).
    vehicle_brand_id: uuid.UUID | None = None
    vehicle_model_id: uuid.UUID | None = None
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    measure_id: uuid.UUID | None = None
    unit: str = Field(min_length=1, max_length=30)
    min_stock: int = Field(default=10, ge=0)


class PartUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=40)
    manufacturer_part_number: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=150)
    category_id: uuid.UUID | None = None
    vehicle_brand_id: uuid.UUID | None = None
    vehicle_model_id: uuid.UUID | None = None
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    measure_id: uuid.UUID | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=30)
    min_stock: int | None = Field(default=None, ge=0)
    # Clears (sets to None) an optional field the payload can't otherwise
    # distinguish from "leave unchanged" — mirrors ServiceOrderUpdate's clear_*.
    clear_vehicle_brand: bool = False
    clear_vehicle_model: bool = False
    clear_measure: bool = False
    clear_manufacturer_part_number: bool = False
    clear_years: bool = False


class PartRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    manufacturer_part_number: str | None
    name: str
    category_id: uuid.UUID
    category_name: str
    vehicle_brand_id: uuid.UUID | None
    vehicle_brand_name: str | None
    vehicle_model_id: uuid.UUID | None
    vehicle_model_name: str | None
    year_from: int | None
    year_to: int | None
    measure_id: uuid.UUID | None
    measure_name: str | None
    unit: str
    min_stock: int
    is_active: bool
    stock_total: int = 0
    reference_price: float | None = None
    # Ubicación en almacén — jalada del lote recibido más recientemente que
    # registró una, nunca escrita a mano en el catálogo.
    location: str | None = None
    created_at: datetime
    updated_at: datetime


class PartBulkItem(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=150)
    category: str = Field(min_length=1, max_length=60)
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


class PartSaleAllocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lot_id: uuid.UUID
    quantity: int
    unit_cost: float


class PartSaleQuoteLine(BaseModel):
    part_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: int
    unit_price: float
    unit_cost: float
    line_total: float
    allocations: list[PartSaleAllocationRead]


class PartSaleQuoteRead(BaseModel):
    lines: list[PartSaleQuoteLine]
    total: float


class PartSaleQuoteInput(BaseModel):
    filial_id: uuid.UUID
    warehouse_id: uuid.UUID
    discount_label: DiscountLabel = DEFAULT_DISCOUNT
    lines: list[PartSaleLineInput] = Field(min_length=1)


class PartWarrantyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    part_id: uuid.UUID
    lot_id: uuid.UUID
    lot_code: str
    quantity: int
    warranty_days: int
    starts_at: datetime
    expires_at: datetime
    is_active: bool


class PartSaleLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_price: float
    unit_cost: float | None
    warehouse_id: uuid.UUID | None
    line_total: float
    dispatched_quantity: int | None
    allocations: list[PartSaleAllocationRead]
    warranties: list[PartWarrantyRead]


class PartSaleLineDispatch(BaseModel):
    line_id: uuid.UUID
    dispatched_quantity: int = Field(ge=0)


class PartSaleCreate(PartSaleQuoteInput):
    client_name: str = Field(min_length=2, max_length=150)
    client_document: str | None = None
    discount_label: DiscountLabel = DEFAULT_DISCOUNT
    lines: list[PartSaleLineInput] = Field(min_length=1)


class PartSaleUpdate(BaseModel):
    status: PartSaleStatus | None = None
    dispatched_lines: list[PartSaleLineDispatch] | None = None


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
    photo_urls: list[str] = Field(default_factory=list)


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
    photo_urls: list[str]
    created_at: datetime
