import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.service_orders.schemas import OrderSummary


class BillingInput(BaseModel):
    payment_method: Literal["usd", "bs", "mixed"]
    usd_base: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)


class BillingQuote(BaseModel):
    summary: OrderSummary
    payment_method: Literal["usd", "bs", "mixed"]
    billing_date: date
    bcv_rate: float | None
    bcv_date: date | None
    igtf_percentage: float
    igtf_amount: float
    usd_base: float
    bs_base_usd: float
    due_usd: float
    due_bs: float
    total_usd: float
    quote_hash: str


class InvoiceCreate(BillingInput):
    request_id: uuid.UUID
    quote_hash: str = Field(min_length=64, max_length=64)
    paid_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    paid_bs: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    usd_account_id: uuid.UUID | None = None
    bs_account_id: uuid.UUID | None = None
    payment_reference: str = Field(default="", max_length=120)
