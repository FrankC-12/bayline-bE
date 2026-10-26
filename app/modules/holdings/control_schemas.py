import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
Section = Literal["tesoreria", "comercial", "inventario", "taller", "contabilidad", "actividad"]


class Target(BaseModel):
    kind: Literal["target"]
    filial_id: uuid.UUID
    amount: Money


class CashCount(BaseModel):
    kind: Literal["cash_count"]
    filial_id: uuid.UUID
    account_id: uuid.UUID
    counted: Money
    note: str = Field(default="", max_length=500)


class TaxObligation(BaseModel):
    kind: Literal["tax"]
    filial_id: uuid.UUID
    name: str = Field(min_length=2, max_length=150)
    currency: Literal["usd", "bs", "eur"]
    amount: PositiveMoney
    due_date: date


class TaxPayment(BaseModel):
    kind: Literal["tax_payment"]
    filial_id: uuid.UUID
    obligation_id: uuid.UUID
    account_id: uuid.UUID
    amount: PositiveMoney
    reference: str = Field(min_length=1, max_length=100)


class FundsTransfer(BaseModel):
    kind: Literal["funds_transfer"]
    source_account_id: uuid.UUID
    destination_account_id: uuid.UUID
    amount: PositiveMoney
    reference: str = Field(min_length=1, max_length=100)


class Opportunity(BaseModel):
    kind: Literal["opportunity"]
    filial_id: uuid.UUID
    client: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=2, max_length=300)
    amount: Money
    stage: Literal["contacto", "cotizacion", "negociacion", "ganada", "perdida"]
    opportunity_id: uuid.UUID | None = None


class JournalLine(BaseModel):
    account: str = Field(min_length=2, max_length=120)
    category: Literal["asset", "liability", "equity", "revenue", "cost", "expense"]
    debit: Money = Decimal("0")
    credit: Money = Decimal("0")

    @model_validator(mode="after")
    def one_side(self):
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("Cada línea debe tener un importe en debe o haber, no ambos.")
        return self


class Journal(BaseModel):
    kind: Literal["journal"]
    filial_id: uuid.UUID | None = None
    description: str = Field(min_length=3, max_length=300)
    elimination: bool = False
    lines: list[JournalLine] = Field(min_length=2, max_length=100)

    @model_validator(mode="after")
    def balanced(self):
        if sum(x.debit for x in self.lines) != sum(x.credit for x in self.lines):
            raise ValueError("El asiento debe cuadrar: debe = haber.")
        if self.elimination == (self.filial_id is not None):
            raise ValueError(
                "Un asiento de filial requiere filial; una eliminación es solo consolidada."
            )
        return self


class StockTransferLine(BaseModel):
    source_part_id: uuid.UUID
    destination_part_id: uuid.UUID
    quantity: int = Field(ge=1, le=1000000)


class StockTransfer(BaseModel):
    kind: Literal["stock_transfer"]
    source_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    lines: list[StockTransferLine] = Field(min_length=1, max_length=100)
    note: str = Field(min_length=2, max_length=300)


class StockTransferAction(BaseModel):
    kind: Literal["stock_transfer_action"]
    transfer_id: uuid.UUID
    action: Literal["approve", "dispatch", "receive", "reject"]
    note: str = Field(min_length=2, max_length=300)


class VehicleDelivery(BaseModel):
    kind: Literal["vehicle_delivery"]
    filial_id: uuid.UUID
    vehicle_id: uuid.UUID
    note: str = Field(min_length=2, max_length=300)


class RiskPolicy(BaseModel):
    kind: Literal["risk_policy"]
    max_discount_percentage: Decimal | None = Field(default=None, ge=0, le=100)
    workshop_warning_hours: int = Field(default=48, ge=1, le=8760)


RegisterPayload = Annotated[
    Target
    | CashCount
    | TaxObligation
    | TaxPayment
    | FundsTransfer
    | Opportunity
    | Journal
    | StockTransfer
    | StockTransferAction
    | VehicleDelivery
    | RiskPolicy,
    Field(discriminator="kind"),
]


class RegisterCreate(BaseModel):
    request_id: uuid.UUID
    entry_date: date
    record: RegisterPayload


class RegisterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    entry_date: date
    payload: dict[str, Any]
    created_at: datetime


class Metric(BaseModel):
    label: str
    value: float | int | str | None
    format: Literal["money", "number", "percent", "text"] = "number"
    currency: str = "usd"
    note: str = ""


class Column(BaseModel):
    key: str
    label: str
    format: Literal["money", "number", "percent", "date", "datetime", "text", "status"] = "text"


class ReportTable(BaseModel):
    title: str
    columns: list[Column]
    rows: list[dict[str, Any]]
    note: str = ""


class ControlReport(BaseModel):
    section: Section
    generated_at: datetime
    date_from: date
    date_to: date
    filiales: list[dict[str, str]]
    metrics: list[Metric] = []
    tables: list[ReportTable] = []
    notes: list[str] = []
