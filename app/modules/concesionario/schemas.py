import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    cost_is_estimated: bool = False
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
    # Only meaningful for sale_type=contado — how the payment splits between
    # foreign currency and bolívares, since IGTF only taxes the USD portion.
    # The server computes igtf_amount/final_price from this; a client-sent
    # total is never trusted, the same way service_orders/billing.py freezes
    # its own quote server-side rather than accepting a submitted amount.
    payment_method: Literal["usd", "bs", "mixed"] | None = None
    usd_base: float | None = Field(default=None, ge=0)
    # Only meaningful when the computed final_price comes out below the
    # vehicle's cost_price — the caller must explicitly opt in and justify
    # it; the server itself decides whether the sale actually is below cost.
    below_cost_override: bool = False
    below_cost_override_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_payment_method_for_contado(self) -> "VehicleSaleInput":
        if self.sale_type == SaleType.CONTADO and self.payment_method is None:
            raise ValueError("Indica cómo se cobra (divisas, bolívares o mixto) para una venta de contado.")
        return self


class VehicleReservationInput(BaseModel):
    client_id: uuid.UUID
    # The vendedor the reservation — and the unit — is locked to, distinct
    # from whoever is submitting the request (e.g. an admin reserving on a
    # vendedor's behalf).
    advisor_user_id: uuid.UUID
    deposit_amount: float = Field(gt=0)
    expires_at: date


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
    cost_is_estimated: bool | None = None
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
    cost_is_estimated: bool
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
    reserved_client_id: uuid.UUID | None
    reserved_by_user_id: uuid.UUID | None
    deposit_amount: float | None
    reservation_expires_at: date | None
    reserved_at: datetime | None
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
    payment_method: str | None
    usd_base: float | None
    igtf_amount: float
    bcv_rate: float | None
    final_price: float
    below_cost_override: bool
    below_cost_override_note: str | None
    authorized_by_user_id: uuid.UUID | None
    authorized_at: datetime | None
    created_at: datetime
