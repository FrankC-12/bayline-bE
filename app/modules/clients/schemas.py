import re
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.modules.clients.enums import (
    AddressType,
    ClientType,
    ContactPreference,
    DocumentType,
    FuelType,
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
    mileage: int | None = Field(default=None, ge=0)
    purchase_date: date | None = None
    body_type: str | None = Field(default=None, max_length=40)
    plate: str = Field(max_length=8)
    color: str | None = Field(default=None, max_length=40)
    upholstery: str | None = Field(default=None, max_length=40)
    fuel_type: FuelType | None = None
    transmission: TransmissionType | None = None

    @field_validator("vin")
    @classmethod
    def validate_vin(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.upper().strip()
        if len(v) != 17:
            raise ValueError("El VIN debe tener exactamente 17 caracteres.")
        return v

    @field_validator("plate")
    @classmethod
    def validate_plate(cls, v: str) -> str:
        v = v.upper().strip()
        if len(v) != 8:
            raise ValueError("La placa debe tener exactamente 8 caracteres.")
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
    plate: str
    color: str | None
    upholstery: str | None
    fuel_type: FuelType | None
    transmission: TransmissionType | None


class ClientBase(BaseModel):
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


class ClientUpdate(BaseModel):
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


class ClientRead(BaseModel):
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
    vehicles: list[VehicleRead]
    created_at: datetime
    updated_at: datetime