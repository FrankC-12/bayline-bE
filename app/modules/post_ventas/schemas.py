import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.post_ventas.enums import TemparioCategory, VehicleWarrantySource, WorkshopWarrantyCoverage


class LaborSettingsUpdate(BaseModel):
    hourly_rate: float = Field(ge=0)
    commission_percentage: float = Field(ge=0, le=100)
    igtf_percentage: float = Field(ge=0, le=100)
    iva_percentage: float = Field(ge=0, le=100)
    bcv_rate: float | None = Field(default=None, gt=0)
    part_warranty_days: int = Field(ge=0, default=90)
    vehicle_warranty_default_months: int = Field(ge=0, default=36)
    workshop_warranty_days: int = Field(ge=0, default=90)
    workshop_warranty_km: int = Field(ge=0, default=5000)
    workshop_parts_warranty_days: int = Field(ge=0, default=90)
    workshop_parts_warranty_km: int = Field(ge=0, default=5000)
    manual_movement_attachment_threshold_usd: float = Field(ge=0, default=100)
    iva_retention_default_percentage: float = Field(ge=0, le=100, default=0)
    islr_retention_default_percentage: float = Field(ge=0, le=100, default=0)


class LaborSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    filial_id: uuid.UUID
    hourly_rate: float
    commission_percentage: float
    igtf_percentage: float
    iva_percentage: float
    bcv_rate: float
    bcv_rate_date: date | None
    bcv_rate_is_stale: bool
    part_warranty_days: int
    vehicle_warranty_default_months: int
    workshop_warranty_days: int
    workshop_warranty_km: int
    workshop_parts_warranty_days: int
    workshop_parts_warranty_km: int
    manual_movement_attachment_threshold_usd: float
    iva_retention_default_percentage: float
    islr_retention_default_percentage: float
    updated_at: datetime


class CompatibleVehicle(BaseModel):
    brand: str
    model: str


class TemparioPartInput(BaseModel):
    part_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=150)
    quantity: int = Field(ge=1, default=1)
    unit_cost: float = Field(ge=0, default=0)


class TemparioPartRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID | None
    name: str
    quantity: int
    unit_cost: float
    subtotal: float


class TemparioCreate(BaseModel):
    filial_id: uuid.UUID
    category: TemparioCategory
    sequence_number: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=2, max_length=150)
    estimated_hours: float = Field(ge=0)
    year_from: int | None = None
    year_to: int | None = None
    compatible_vehicles: list[CompatibleVehicle] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    requires_parts: bool = True
    parts: list[TemparioPartInput] = Field(default_factory=list)


class TemparioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=150)
    estimated_hours: float | None = Field(default=None, ge=0)
    year_from: int | None = None
    year_to: int | None = None
    compatible_vehicles: list[CompatibleVehicle] | None = None
    tools: list[str] | None = None
    requires_parts: bool | None = None
    parts: list[TemparioPartInput] | None = None


class TemparioRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    category: TemparioCategory
    name: str
    estimated_hours: float
    year_from: int | None
    year_to: int | None
    compatible_vehicles: list[CompatibleVehicle]
    tools: list[str]
    requires_parts: bool
    parts: list[TemparioPartRead]
    parts_cost: float
    parts_margin: float
    labor_cost: float
    total_price: float
    created_at: datetime
    updated_at: datetime


class MaintenancePlanEntryInput(BaseModel):
    tempario_id: uuid.UUID
    interval_km: int | None = Field(default=None, ge=0)
    interval_months: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_an_interval(self):
        if self.interval_km is None and self.interval_months is None:
            raise ValueError("Define los kilómetros o los meses a los que toca este servicio.")
        return self


class MaintenancePlanEntryRead(BaseModel):
    id: uuid.UUID
    tempario_id: uuid.UUID
    tempario_code: str
    tempario_name: str
    interval_km: int | None
    interval_months: int | None


class MaintenancePlanCreate(BaseModel):
    filial_id: uuid.UUID
    brand: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=150)
    entries: list[MaintenancePlanEntryInput] = Field(default_factory=list)


class MaintenancePlanUpdate(BaseModel):
    brand: str | None = Field(default=None, min_length=1, max_length=60)
    name: str | None = Field(default=None, min_length=1, max_length=150)
    entries: list[MaintenancePlanEntryInput] | None = None


class MaintenancePlanRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    brand: str
    name: str
    entries: list[MaintenancePlanEntryRead]
    created_at: datetime
    updated_at: datetime


def _validate_vin(v: str) -> str:
    v = v.strip().upper()
    if len(v) != 17:
        raise ValueError("El VIN debe tener exactamente 17 caracteres.")
    return v


class VehicleWarrantyCreate(BaseModel):
    filial_id: uuid.UUID
    vin: str
    brand: str = Field(min_length=1, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    starts_at: date
    duration_months: int | None = Field(default=None, ge=0)
    duration_km: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=300)

    _validate_vin = field_validator("vin")(_validate_vin)

    @model_validator(mode="after")
    def _require_a_duration(self):
        if self.duration_months is None and self.duration_km is None:
            raise ValueError("Define la duración en meses o en kilómetros de la garantía.")
        return self


class VehicleWarrantyRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    vin: str
    brand: str
    model: str | None
    starts_at: date
    duration_months: int | None
    duration_km: int | None
    expires_at: date | None
    status: str
    days_remaining: int | None = None
    km_remaining: int | None = None
    source: VehicleWarrantySource
    dealership_vehicle_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class VehicleWarrantyBulkItem(BaseModel):
    vin: str
    brand: str = Field(min_length=1, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    starts_at: date
    duration_months: int | None = Field(default=None, ge=0)
    duration_km: int | None = Field(default=None, ge=0)
    note: str | None = Field(default=None, max_length=300)

    _validate_vin = field_validator("vin")(_validate_vin)

    @model_validator(mode="after")
    def _require_a_duration(self):
        if self.duration_months is None and self.duration_km is None:
            raise ValueError("Define la duración en meses o en kilómetros de la garantía.")
        return self


class VehicleWarrantyBulkCreate(BaseModel):
    filial_id: uuid.UUID
    items: list[VehicleWarrantyBulkItem] = Field(min_length=1, max_length=500)


class VehicleWarrantyBulkResult(BaseModel):
    created: list[VehicleWarrantyRead]
    skipped: list[str]


class WorkshopWarrantyRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    vin: str
    service_order_id: uuid.UUID
    service_order_task_id: uuid.UUID
    coverage_type: WorkshopWarrantyCoverage
    tempario_code_snapshot: str
    tempario_name_snapshot: str
    technician_user_id: uuid.UUID | None
    starts_at: date
    duration_days: int
    duration_km: int
    expires_at: date
    expiration_mileage: int | None
    status: str
    days_remaining: int
    created_at: datetime