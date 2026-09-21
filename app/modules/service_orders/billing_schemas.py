import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.modules.service_orders.schemas import OrderSummary


class BillingInput(BaseModel):
    payment_method: Literal["usd", "bs", "mixed"]
    usd_base: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    # None/None = bill the vehicle's own owner (today's behavior). Set
    # billed_client_id to bill a different Client in the same filial instead
    # — e.g. the holding, registered as a regular empresa client. Set
    # billed_supplier_id to bill a Supplier instead (e.g. the manufacturer
    # or a parts supplier covering a warranty claim) — resolved to its
    # linked billing Client, created on first use. At most one of the two
    # may be set.
    billed_client_id: uuid.UUID | None = None
    billed_supplier_id: uuid.UUID | None = None
    # A contribuyente especial (typically any empresa-type client, e.g. an
    # importador) withholds part of the IVA and ISLR when it pays — these
    # percentages let the quote/invoice show what will actually be received,
    # not just the nominal total. 0 for a particular client that isn't a
    # withholding agent.
    iva_retention_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)
    islr_retention_percentage: Decimal = Field(default=Decimal("0"), ge=0, le=100, max_digits=5, decimal_places=2)

    @model_validator(mode="after")
    def _billed_target_is_unambiguous(self) -> "BillingInput":
        if self.billed_client_id is not None and self.billed_supplier_id is not None:
            raise ValueError("Elige un cliente o un proveedor para facturar, no ambos.")
        return self


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
    # "service_order_invoice" has an actual collect-payment action (see
    # CollectInvoicePaymentInput/collect_invoice); "part_sale" is informational
    # only here — it clears once the sale itself is marked completado.
    document_type: Literal["service_order_invoice", "part_sale"] = "service_order_invoice"
    invoice_id: uuid.UUID  # the document's own id (invoice.id, or sale.id for a part_sale)
    code: str
    service_order_id: uuid.UUID | None = None
    order_code: str | None = None
    filial_id: uuid.UUID
    billed_client_id: uuid.UUID | None = None
    billed_client_name: str
    total_usd: float
    iva_retention_amount: float
    islr_retention_amount: float
    net_expected: float
    amount_paid_at_issuance: float
    pending_amount: float
    issued_at: datetime
    days_outstanding: int
    aging_bucket: Literal["0-30", "31-60", "61-90", "90+"]


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
