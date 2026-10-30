import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.modules.clients.schemas import VehicleInput
from app.modules.concesionario.enums import (
    FuelType,
    SaleType,
    TransmissionType,
    VehicleCondition,
    VehicleLocation,
    VehicleStatus,
)


class VehicleCreate(BaseModel):
    @field_validator("vin")
    @classmethod
    def valid_vin(cls, value):
        return VehicleInput.validate_vin(value)

    @field_validator("year")
    @classmethod
    def valid_year(cls, value):
        return VehicleInput.validate_year(value)

    filial_id: uuid.UUID
    status: VehicleStatus = VehicleStatus.EN_TRANSITO
    condition: VehicleCondition
    location: VehicleLocation
    brand: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    version: str | None = Field(default=None, max_length=60)
    year: int = Field(ge=1980, le=2100)
    mileage: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    engine_type: str | None = Field(default=None, max_length=100)
    seats: int | None = Field(default=None, ge=1, le=100, strict=True)
    upholstery: str | None = Field(default=None, max_length=60)
    previous_owners: int | None = Field(default=None, ge=0, le=100, strict=True)
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

    @model_validator(mode="after")
    def _require_used_mileage(self):
        if self.condition == VehicleCondition.USADO and self.mileage is None:
            raise ValueError("El kilometraje es obligatorio para un vehículo usado.")
        if self.condition == VehicleCondition.USADO and self.previous_owners is None:
            raise ValueError("Indica la cantidad de dueños anteriores del vehículo usado.")
        if self.condition != VehicleCondition.USADO and self.previous_owners is not None:
            raise ValueError("Los dueños anteriores solo corresponden a un vehículo usado.")
        return self


class VehicleSaleInput(BaseModel):
    usd_account_id: uuid.UUID | None = None
    bs_account_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    opportunity_version: int | None = Field(default=None, ge=1)
    client_id: uuid.UUID | None = None
    client_name: str = Field(min_length=2, max_length=150)
    client_document: str | None = None
    advisor_user_id: uuid.UUID
    sale_type: SaleType
    # Only meaningful for sale_type=contado — how the payment splits between
    # foreign currency and bolívares, since IGTF only taxes the USD portion.
    # The server computes igtf_amount/final_price from this; a client-sent
    # total is never trusted, the same way service_orders/billing.py freezes
    # its own quote server-side rather than accepting a submitted amount.
    payment_method: Literal["usd", "bs", "mixed"] | None = None
    usd_payment_channel: Literal["cash", "binance", "paypal", "zelle"] | None = None
    bs_payment_channel: Literal["pago_movil", "transferencia"] | None = None
    usd_base: float | None = Field(default=None, ge=0)
    # Only meaningful when the computed final_price comes out below the
    # vehicle's cost_price — the caller must explicitly opt in and justify
    # it; the server itself decides whether the sale actually is below cost.
    below_cost_override: bool = False
    below_cost_override_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check_payment_method_for_contado(self) -> "VehicleSaleInput":
        if self.sale_type == SaleType.CONTADO and self.payment_method is None:
            raise ValueError(
                "Indica cómo se cobra (divisas, bolívares o mixto) para una venta de contado."
            )
        if self.sale_type == SaleType.CONTADO:
            if self.payment_method in ("usd", "mixed") and not self.usd_payment_channel:
                raise ValueError("Indica el método de pago en USD.")
            if self.payment_method in ("bs", "mixed") and not self.bs_payment_channel:
                raise ValueError("Indica el método de pago en bolívares.")
            if self.payment_method == "usd" and self.bs_payment_channel:
                raise ValueError("Un pago en USD no incluye un método en bolívares.")
            if self.payment_method == "bs" and self.usd_payment_channel:
                raise ValueError("Un pago en bolívares no incluye un método en USD.")
        elif self.usd_payment_channel or self.bs_payment_channel:
            raise ValueError("El pago financiado se gestiona con la financiera.")
        return self


class VehiclePhotoRemoveInput(BaseModel):
    photo_url: str


class VehicleReservationInput(BaseModel):
    opportunity_version: int | None = Field(default=None, ge=1)
    account_id: uuid.UUID | None = None
    payment_reference: str | None = Field(default=None, min_length=3, max_length=60)
    payment_channel: Literal["cash", "binance", "paypal", "zelle"] | None = None
    client_id: uuid.UUID
    # The vendedor the reservation — and the unit — is locked to, distinct
    # from whoever is submitting the request (e.g. an admin reserving on a
    # vendedor's behalf).
    advisor_user_id: uuid.UUID
    deposit_amount: float = Field(gt=0, allow_inf_nan=False)
    expires_at: date
    reason: str = Field(default="Reserva de cliente", min_length=3, max_length=500)


class VehicleUpdate(BaseModel):
    @field_validator("vin")
    @classmethod
    def valid_vin(cls, value):
        return VehicleInput.validate_vin(value) if value is not None else value

    @field_validator("year")
    @classmethod
    def valid_year(cls, value):
        return VehicleInput.validate_year(value)

    vin: str | None = Field(default=None, min_length=17, max_length=17)
    sku: str | None = Field(default=None, min_length=1, max_length=40)
    version: str | None = Field(default=None, max_length=60)
    status_reason: str | None = Field(default=None, max_length=500)
    status: VehicleStatus | None = None
    condition: VehicleCondition | None = None
    location: VehicleLocation | None = None
    brand: str | None = Field(default=None, min_length=1, max_length=60)
    model: str | None = Field(default=None, min_length=1, max_length=60)
    year: int | None = Field(default=None, ge=1980, le=2100)
    mileage: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    engine_type: str | None = Field(default=None, max_length=100)
    seats: int | None = Field(default=None, ge=1, le=100, strict=True)
    upholstery: str | None = Field(default=None, max_length=60)
    previous_owners: int | None = Field(default=None, ge=0, le=100, strict=True)
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
    @field_serializer("registration_documents")
    def public_document_links(self, documents):
        from app.core.config import get_settings

        prefix = get_settings().api_v1_prefix
        if self.condition != VehicleCondition.USADO:
            return {}
        return {
            kind: f"{prefix}/dealership-vehicles/{self.id}/documents/{kind}" for kind in documents
        }

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    status: VehicleStatus
    condition: VehicleCondition
    location: VehicleLocation | None
    brand: str
    model: str
    version: str | None = None
    year: int
    mileage: int | None
    engine_type: str | None = Field(default=None, max_length=100)
    seats: int | None = Field(default=None, ge=1, le=100, strict=True)
    upholstery: str | None = Field(default=None, max_length=60)
    previous_owners: int | None = Field(default=None, ge=0, le=100, strict=True)
    registration_documents: dict = Field(default_factory=dict)
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
    mileage_at_sale: int | None
    client_name: str
    client_document: str | None
    advisor_user_id: uuid.UUID | None
    sale_type: SaleType
    payment_method: str | None
    usd_payment_channel: str | None = None
    bs_payment_channel: str | None = None
    usd_base: float | None
    igtf_amount: float
    bcv_rate: float | None
    final_price: float
    below_cost_override: bool
    below_cost_override_note: str | None
    authorized_by_user_id: uuid.UUID | None
    authorized_at: datetime | None
    created_at: datetime


class VehicleStatusEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    previous_status: VehicleStatus | None
    new_status: VehicleStatus
    user_id: uuid.UUID
    user_name: str
    reason: str
    reservation_snapshot: dict | None
    created_at: datetime
