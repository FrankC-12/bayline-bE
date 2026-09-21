import uuid
from datetime import date

from pydantic import BaseModel


class KpiRow(BaseModel):
    user_id: uuid.UUID
    count: int
    avg_hours: float


class KpiReport(BaseModel):
    rows: list[KpiRow]
    overall_count: int
    overall_avg_hours: float


class MaintenanceDueRow(BaseModel):
    vehicle_id: uuid.UUID
    plate: str | None
    brand: str
    model: str
    client_id: uuid.UUID
    client_name: str
    phone_primary: str
    phone_secondary: str | None
    due_at: date
    days_until_due: int


class MaintenanceDueReport(BaseModel):
    window_days: int
    rows: list[MaintenanceDueRow]
    overdue_count: int


class ReworkTechnicianRow(BaseModel):
    user_id: uuid.UUID
    invoiced_orders_count: int
    claims_count: int
    rework_rate: float
    avg_days_to_claim: float | None


class ReworkServiceRow(BaseModel):
    tempario_id: uuid.UUID
    tempario_name: str
    claims_count: int
    avg_days_to_claim: float | None


class ReworkPartRow(BaseModel):
    part_id: uuid.UUID
    part_name: str
    claims_count: int
    avg_days_to_claim: float | None


class ReworkReport(BaseModel):
    invoiced_orders_count: int
    orders_with_claim_count: int
    rework_rate: float
    avg_days_to_claim: float | None
    # A quick heuristic split on the days-to-claim distribution: a claim that
    # lands within ~30 days more likely points at workmanship; further out,
    # more likely normal wear. Not a substitute for reading the breakdown.
    quick_claims_count: int
    slow_claims_count: int
    by_technician: list[ReworkTechnicianRow]
    by_service: list[ReworkServiceRow]
    by_part: list[ReworkPartRow]


class ManualMovementsRate(BaseModel):
    total_count: int
    manual_count: int
    rate: float