import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.concesionario.enums import (
    FuelType,
    SaleType,
    TransmissionType,
    VehicleCondition,
    VehicleStatus,
)


class VehicleCreate(BaseModel):
    filial_id: uuid.UUID
    status: VehicleStatus = VehicleStatus.EN_TRANSITO
    condition: VehicleCondition
    brand: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    year: int = Field(ge=1980, le=2100)
    color: str | None = Field(default=None, max_length=40)
    fuel_type: FuelType | None = None
    transmission: TransmissionType | None = None
    vin: str = Field(min_length=1, max_length=17)
    plate: str | None = Field(default=None, max_length=10)
    sku: str = Field(min_length=1, max_length=40)
    price_cash: float = Field(ge=0)
    price_financed: float = Field(ge=0)
    cost_price: float = Field(gt=0)
    price_currency: str = Field(default="USD", pattern="^(USD|VES)$")
    iva_percentage: float = Field(default=16, ge=0, le=100)
    igtf_percentage: float = Field(default=3, ge=0, le=100)
    luxury_tax_percentage: float = Field(default=0, ge=0, le=100)
    financing_provider: str | None = Field(default="troyano", max_length=50)


class VehicleSaleInput(BaseModel):
    client_name: str = Field(min_length=2, max_length=150)
    client_document: str | None = None
    advisor_user_id: uuid.UUID | None = None
    sale_type: SaleType
    final_price: float = Field(ge=0)


class VehicleUpdate(BaseModel):
    status: VehicleStatus | None = None
    condition: VehicleCondition | None = None
    brand: str | None = Field(default=None, min_length=1, max_length=60)
    model: str | None = Field(default=None, min_length=1, max_length=60)
    year: int | None = Field(default=None, ge=1980, le=2100)
    color: str | None = Field(default=None, max_length=40)
    fuel_type: FuelType | None = None
    transmission: TransmissionType | None = None
    plate: str | None = Field(default=None, max_length=10)
    price_cash: float | None = Field(default=None, ge=0)
    price_financed: float | None = Field(default=None, ge=0)
    cost_price: float | None = Field(default=None, ge=0)
    price_currency: str | None = Field(default=None, pattern="^(USD|VES)$")
    iva_percentage: float | None = Field(default=None, ge=0, le=100)
    igtf_percentage: float | None = Field(default=None, ge=0, le=100)
    luxury_tax_percentage: float | None = Field(default=None, ge=0, le=100)
    financing_provider: str | None = Field(default=None, max_length=50)
    financing_external_id: str | None = Field(default=None, max_length=100)
    # Required when `status` is being set to VENDIDO for the first time.
    sale: VehicleSaleInput | None = None


class VehicleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    status: VehicleStatus
    condition: VehicleCondition
    brand: str
    model: str
    year: int
    color: str | None
    fuel_type: FuelType | None
    transmission: TransmissionType | None
    vin: str
    plate: str | None
    sku: str
    price_cash: float
    price_financed: float
    cost_price: float | None
    price_currency: str
    iva_percentage: float
    igtf_percentage: float
    luxury_tax_percentage: float
    iva_amount: float
    igtf_amount: float
    luxury_tax_amount: float
    cash_total: float
    financing_provider: str | None
    financing_external_id: str | None
    images: list[str]
    created_at: datetime
    updated_at: datetime


class VehicleSaleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    vehicle_id: uuid.UUID
    client_name: str
    client_document: str | None
    advisor_user_id: uuid.UUID | None
    sale_type: SaleType
    final_price: float
    created_at: datetime
