import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Condition(StrEnum):
    GOOD = "bien"
    FAIR = "regular"
    DEFECTIVE = "defectuoso"


class BrakeCondition(StrEnum):
    NOT_APPLICABLE = "no_aplica"
    GOOD = "bien"
    FAIR = "regular"
    DEFECTIVE = "defectuoso"


class BatteryStatus(StrEnum):
    CHARGED = "cargada"
    DISCHARGED = "descargada"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Engine(StrictModel):
    leaks: Condition | None = None
    accessory_belt: Condition | None = None
    air_filter: Condition | None = None
    battery: BatteryStatus | None = None
    battery_voltage: float | None = Field(
        None, ge=0, allow_inf_nan=False, description="Voltios (V)"
    )


class Underbody(StrictModel):
    steering_box: Condition | None = None
    tie_rod_ends: Condition | None = None
    dust_boots: Condition | None = None
    pipes: Condition | None = None
    leaks: Condition | None = None
    exhaust_pipe: Condition | None = None
    suspension: Condition | None = None
    tripod_joints: Condition | None = None


class Tire(StrictModel):
    pressure: float | None = Field(None, ge=0, allow_inf_nan=False, description="Presión (psi)")

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_depth(cls, value):
        if isinstance(value, dict):
            value = {key: item for key, item in value.items() if key != "tread_depth"}
        return value


class Tires(StrictModel):
    front_left: Tire = Field(default_factory=Tire)
    front_right: Tire = Field(default_factory=Tire)
    rear_left: Tire = Field(default_factory=Tire)
    rear_right: Tire = Field(default_factory=Tire)
    spare: Tire = Field(default_factory=Tire)


class Brakes(StrictModel):
    front_pads: BrakeCondition | None = None
    front_shoes: BrakeCondition | None = None
    rear_pads: BrakeCondition | None = None
    rear_shoes: BrakeCondition | None = None


class FluidReading(StrictModel):
    level: str | float | None = None
    condition: str | None = Field(None, max_length=100)
    color: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=5000)
    photo_urls: list[str] = Field(default_factory=list, max_length=20)


class InspectionData(StrictModel):
    interior: dict = Field(default_factory=dict, max_length=100)
    fluids: dict[str, FluidReading | str] = Field(default_factory=dict, max_length=30)
    photo_urls: list[str] = Field(default_factory=list, max_length=50)
    engine: Engine = Field(default_factory=Engine)
    underbody: Underbody = Field(default_factory=Underbody)
    tires: Tires = Field(default_factory=Tires)
    brakes: Brakes = Field(default_factory=Brakes)
    observations: str | None = Field(None, max_length=5000)


class AnnexRead(BaseModel):
    id: uuid.UUID
    filename: str
    url: str
    uploaded_at: datetime
    uploaded_by_user_id: uuid.UUID


class DetailedInspectionRead(BaseModel):
    id: uuid.UUID
    service_order_id: uuid.UUID
    vehicle_id: uuid.UUID
    plate: str | None
    vehicle_label: str
    status: str
    data: InspectionData
    sections_complete: dict[str, bool]
    completed_steps: int
    total_steps: int = 5
    missing_fields: list[str]
    annexes: list[AnnexRead]
    created_by_user_id: uuid.UUID
    updated_by_user_id: uuid.UUID
    completed_by_user_id: uuid.UUID | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
