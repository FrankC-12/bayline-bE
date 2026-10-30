import re
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.clients.enums import (
    AddressType,
    ClientType,
    ContactPreference,
    DocumentType,
    FuelType,
    MaintenancePlanEntryStatus,
    TransmissionType,
)

DOCUMENT_REGEX = re.compile(r"^\d{6,9}$")
PHONE_REGEX = re.compile(r"^0\d{10}$")


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


class VehicleInput(BaseModel):
    id: uuid.UUID | None = None
    brand: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    year: int | None = Field(default=None, ge=1980, le=2100)
    vin: str | None = Field(default=None, max_length=17)
    mileage: int | None = Field(default=None, ge=0, le=1_500_000)
    purchase_date: date | None = None
    body_type: str | None = Field(default=None, max_length=40)
    plate: str | None = Field(default=None, max_length=8)
    color: str | None = Field(default=None, max_length=40)
    upholstery: str | None = Field(default=None, max_length=40)
    fuel_type: FuelType | None = None
    transmission: TransmissionType | None = None

    @field_validator("year")
    @classmethod
    def validate_year(cls, v):
        if v is not None and v > date.today().year + 1:
            raise ValueError("El año no puede superar el próximo año modelo.")
        return v

    @field_validator("vin")
    @classmethod
    def validate_vin(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.upper().strip()
        if len(v) != 17:
            raise ValueError("El VIN debe tener exactamente 17 caracteres.")
        if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", v):
            raise ValueError(
                "El VIN debe tener exactamente 17 caracteres alfanuméricos y no puede contener I, O ni Q."
            )
        return v

    @field_validator("plate")
    @classmethod
    def validate_plate(cls, v: str | None) -> str | None:
        # Plate is optional ("sin placa" — e.g. a vehicle not yet
        # registered). Real Venezuelan plate formats range from ~2 chars
        # (poder público) to 8 (diplomática) depending on type — see
        # frontend/src/lib/venezuela-plate.ts, which already validates the
        # exact format client-side, only when the field is actually being
        # edited. This only normalizes and enforces the column width.
        if not v:
            return None
        v = re.sub(r"[\s-]", "", v.upper())
        if not v:
            return None
        if not re.fullmatch(r"[A-Z0-9]{2,8}", v):
            raise ValueError("La placa debe contener de 2 a 8 letras o números.")
        return v


class VehicleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand: str
    model: str
    year: int | None
    vin: str | None
    mileage: int | None
    purchase_date: date | None
    body_type: str | None
    plate: str | None
    color: str | None
    upholstery: str | None
    fuel_type: FuelType | None
    transmission: TransmissionType | None
    current_mileage: int | None = None
    current_mileage_visit_date: date | None = None
    current_mileage_service_order_id: uuid.UUID | None = None
    current_mileage_service_order_code: str | None = None
    next_maintenance_due_at: date | None = None
    next_maintenance_tempario_id: uuid.UUID | None = None
    next_maintenance_tempario_code: str | None = None
    next_maintenance_tempario_name: str | None = None
    maintenance_plan_id: uuid.UUID | None = None
    maintenance_plan_brand: str | None = None
    maintenance_plan_name: str | None = None


class ServiceVehicleRead(BaseModel):
    vehicle: VehicleRead
    client_name: str


class VehicleMaintenancePlanAssign(BaseModel):
    plan_id: uuid.UUID | None = None


class VehicleMileageHistoryEntry(BaseModel):
    """One recorded odometer reading for a vehicle — sourced from a
    PreliminaryInspection, never overwritten, so the full history simply
    accumulates one entry per inspection that captured a mileage."""

    inspection_id: uuid.UUID
    mileage: int
    recorded_at: datetime
    service_order_id: uuid.UUID | None
    service_order_code: str | None


class VehiclePlanEntryStatusRead(BaseModel):
    entry_id: uuid.UUID
    tempario_id: uuid.UUID
    tempario_code: str
    tempario_name: str
    interval_km: int | None
    interval_months: int | None
    status: MaintenancePlanEntryStatus
    completed_at: datetime | None = None
    completed_service_order_code: str | None = None


class VehiclePlanStatusRead(BaseModel):
    vehicle_id: uuid.UUID
    plan_id: uuid.UUID | None
    plan_brand: str | None
    plan_name: str | None
    current_mileage: int | None
    reference_date: date | None
    entries: list[VehiclePlanEntryStatusRead]


LeadOrigin = Literal[
    "registro_directo", "walk_in", "whatsapp", "referido", "marketplace", "taller", "otro"
]
LeadInterest = Literal["vehiculo", "taller", "repuestos", "otro"]
LeadStatus = Literal["nuevo", "en_seguimiento", "descartado"]


class LeadMetadata(BaseModel):
    lead_origin: LeadOrigin = "registro_directo"
    lead_interest: LeadInterest = "otro"
    lead_vehicle_interest: str | None = Field(default=None, max_length=255)
    lead_notes: str | None = Field(default=None, max_length=3000)
    lead_assigned_user_id: uuid.UUID | None = None
    lead_next_contact_at: date | None = None


class ClientBase(LeadMetadata):
    full_name: str = Field(min_length=2, max_length=150)
    client_type: ClientType
    document_type: DocumentType
    document_number: str = Field(min_length=6, max_length=15)
    email: EmailStr | None = None
    phone_primary: str
    phone_secondary: str | None = None
    contact_preference: ContactPreference | None = None
    address: str = Field(min_length=3, max_length=255)
    address_type: AddressType | None = None
    is_holding_billing: bool = False

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value):
        value = value.strip()
        if len(value) < 2:
            raise ValueError("El nombre completo es obligatorio.")
        return value

    @field_validator("document_number")
    @classmethod
    def validate_document_number(cls, v: str) -> str:
        digits = _digits_only(v)
        if not DOCUMENT_REGEX.match(digits):
            raise ValueError("El número de documento debe tener entre 6 y 9 dígitos.")
        return digits

    @field_validator("phone_primary")
    @classmethod
    def validate_phone_primary(cls, v: str) -> str:
        digits = _digits_only(v)
        if not PHONE_REGEX.match(digits):
            raise ValueError("El teléfono debe tener 11 dígitos y empezar con 0 (ej: 04141234567).")
        return digits

    @field_validator("phone_secondary")
    @classmethod
    def validate_phone_secondary(cls, v: str | None) -> str | None:
        if not v:
            return None
        digits = _digits_only(v)
        if not PHONE_REGEX.match(digits):
            raise ValueError("El teléfono debe tener 11 dígitos y empezar con 0.")
        return digits


class ClientCreate(ClientBase):
    filial_id: uuid.UUID
    vehicles: list[VehicleInput] = Field(default_factory=list)


class LeadCreate(ClientCreate):
    address: str = Field(default="", max_length=255)


class LeadUpdate(BaseModel):
    lead_origin: LeadOrigin | None = None
    lead_interest: LeadInterest | None = None
    lead_vehicle_interest: str | None = Field(default=None, max_length=255)
    lead_notes: str | None = Field(default=None, max_length=3000)
    lead_assigned_user_id: uuid.UUID | None = None
    lead_next_contact_at: date | None = None
    lead_status: LeadStatus | None = None

    @field_validator("lead_origin", "lead_interest", "lead_status")
    @classmethod
    def no_null_status(cls, value):
        if value is None:
            raise ValueError("Este campo no puede estar vacío.")
        return value


class ClientUpdate(BaseModel):
    expected_updated_at: datetime | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=150)
    client_type: ClientType | None = None
    document_type: DocumentType | None = None
    document_number: str | None = None
    email: EmailStr | None = None
    phone_primary: str | None = None
    phone_secondary: str | None = None
    contact_preference: ContactPreference | None = None
    address: str | None = Field(default=None, min_length=3, max_length=255)
    address_type: AddressType | None = None
    is_holding_billing: bool | None = None
    vehicles: list[VehicleInput] | None = None

    @field_validator("document_number")
    @classmethod
    def validate_document_number(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = _digits_only(v)
        if not DOCUMENT_REGEX.match(digits):
            raise ValueError("El número de documento debe tener entre 6 y 9 dígitos.")
        return digits

    @field_validator("phone_primary")
    @classmethod
    def validate_phone_primary(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = _digits_only(v)
        if not PHONE_REGEX.match(digits):
            raise ValueError("El teléfono debe tener 11 dígitos y empezar con 0.")
        return digits

    @field_validator("phone_secondary")
    @classmethod
    def validate_phone_secondary(cls, v: str | None) -> str | None:
        if not v:
            return None
        digits = _digits_only(v)
        if not PHONE_REGEX.match(digits):
            raise ValueError("El teléfono debe tener 11 dígitos y empezar con 0.")
        return digits


class ClientRead(LeadMetadata):
    lead_assigned_user_name: str | None = None
    is_lead: bool = False
    lead_status: LeadStatus = "nuevo"
    created_by_user_id: uuid.UUID | None = None
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    full_name: str
    client_type: ClientType
    document_type: DocumentType
    document_number: str
    email: str | None
    phone_primary: str
    phone_secondary: str | None
    contact_preference: ContactPreference | None
    address: str
    address_type: AddressType | None
    is_holding_billing: bool
    vehicles: list[VehicleRead]
    created_at: datetime
    updated_at: datetime


class VehicleServiceHistoryItem(BaseModel):
    id: uuid.UUID
    code: str
    created_at: datetime
    status: str
    type: str
    services: list[str]
    mileage: int | None
    total: float


class VehicleServiceHistoryRead(BaseModel):
    items: list[VehicleServiceHistoryItem]
    total: int
    page: int
    size: int
