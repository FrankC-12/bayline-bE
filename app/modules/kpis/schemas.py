import uuid

from pydantic import BaseModel


class KpiRow(BaseModel):
    user_id: uuid.UUID
    count: int
    avg_hours: float


class KpiReport(BaseModel):
    rows: list[KpiRow]
    overall_count: int
    overall_avg_hours: float