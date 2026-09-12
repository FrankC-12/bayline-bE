import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.service_orders.schemas import OrderSummary


class BillingInput(BaseModel):
    payment_method: Literal["usd", "bs", "mixed"]
    usd_base: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    # None = bill the vehicle's own owner (today's behavior). Set this to bill
    # a different Client in the same filial instead — e.g. the holding,
    # registered as a regular empresa client, for factory-warranty work.
    billed_client_id: uuid.UUID | None = None
    # A contribuyente especial (typically any empresa-type client, e.g. an
    # importador) withholds part of the IVA and ISLR when it pays — these
    # percentages let the quote/invoice show what will actually be received,
    # not just the nominal total. 0 for a particular client that isn't a
    # withholding agent.
    iva_retention_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    islr_retention_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)


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
    iva_retention_percentage: float
    iva_retention_amount: float
    islr_retention_percentage: float
    islr_retention_amount: float
    net_expected: float
    quote_hash: str


class InvoiceCreate(BillingInput):
    request_id: uuid.UUID
    quote_hash: str = Field(min_length=64, max_length=64)
    paid_usd: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    paid_bs: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    usd_account_id: uuid.UUID | None = None
    bs_account_id: uuid.UUID | None = None
    payment_reference: str = Field(default="", max_length=120)
    client_confirmed: bool = False
    client_confirmed_note: str | None = Field(default=None, max_length=200)


class ReceivableRead(BaseModel):
    invoice_id: uuid.UUID
    code: str
    service_order_id: uuid.UUID
    order_code: str
    filial_id: uuid.UUID
    billed_client_id: uuid.UUID
    billed_client_name: str
    total_usd: float
    iva_retention_amount: float
    islr_retention_amount: float
    net_expected: float
    amount_paid_at_issuance: float
    pending_amount: float
    issued_at: datetime


class CollectInvoicePaymentInput(BaseModel):
    account_id: uuid.UUID
    withholding_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    net_collected_amount: Decimal = Field(ge=0, max_digits=18, decimal_places=2)


class HoldingWarrantyFilialReceivables(BaseModel):
    filial_id: uuid.UUID
    filial_name: str
    total_invoiced: float
    total_pending: float
    total_collected: float
    total_withheld: float


class HoldingWarrantyReceivablesReport(BaseModel):
    holding_id: uuid.UUID
    filiales: list[HoldingWarrantyFilialReceivables]
