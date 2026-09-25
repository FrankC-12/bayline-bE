import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.compras.enums import VehiclePurchaseOrderStatus


class VehiclePurchaseOrderLineInput(BaseModel):
    brand: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    version: str | None = None
    year: int = Field(ge=1980, le=2100)
    color: str | None = None
    quantity: int = Field(ge=1)


class VehiclePurchaseOrderCreate(BaseModel):
    filial_id: uuid.UUID
    supplier_id: uuid.UUID
    lines: list[VehiclePurchaseOrderLineInput] = Field(min_length=1)


class VehiclePurchaseOrderLineRead(BaseModel):
    id: uuid.UUID
    brand: str
    model: str
    version: str | None
    year: int
    color: str | None
    quantity: int
    quantity_received: int


class VehiclePurchaseOrderRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    supplier_id: uuid.UUID
    status: VehiclePurchaseOrderStatus
    lines: list[VehiclePurchaseOrderLineRead]
    created_at: datetime


class ReceivedUnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    purchase_order_line_id: uuid.UUID | None
    vin: str
    brand: str
    model: str
    year: int
    color: str | None
    cost_price: float | None
    cost_is_estimated: bool
    purchase_order_invoice_id: uuid.UUID | None


class ReceptionUnitInput(BaseModel):
    purchase_order_line_id: uuid.UUID
    vin: str = Field(min_length=1, max_length=17)


class ReceptionCreate(BaseModel):
    notes: str | None = None
    units: list[ReceptionUnitInput] = Field(min_length=1)


class ReceptionRead(BaseModel):
    id: uuid.UUID
    received_at: datetime
    notes: str | None
    units: list[ReceivedUnitRead]


class VehiclePurchaseOrderDetailRead(VehiclePurchaseOrderRead):
    receptions: list[ReceptionRead]
    invoices: list["VehiclePurchaseOrderInvoiceRead"]


class VehiclePurchaseOrderInvoiceCreate(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=60)
    total_amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    issued_at: date
    # Which received-but-uninvoiced units this invoice covers — omit to
    # cover every currently-uninvoiced unit of the OC.
    dealership_vehicle_ids: list[uuid.UUID] | None = None


class VehiclePurchaseOrderInvoiceRead(BaseModel):
    id: uuid.UUID
    invoice_number: str
    total_amount: float
    currency: str
    issued_at: date
    unit_count: int
    created_at: datetime
