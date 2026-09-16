import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.administracion.enums import (
    AccountCurrency,
    AccountType,
    ClaimResolution,
    ClaimStatus,
    CounterpartyType,
    ExpenseCategory,
    IncomeConcept,
    IncomeSource,
    MovementSourceType,
    PurchaseRequestStatus,
    SupplierStatus,
    SupplierPaymentMethod,
    SupplierType,
    WarrantySubmissionStatus,
)


def _validate_counterparty(self):
    """Shared by IncomeEntryCreate/ExpenseEntryCreate — cliente/proveedor
    reference the existing catalogs, tercero/socio are free text since
    neither has a catalog to reference. Clears whichever fields don't
    match the chosen type so a stale value from switching the dropdown
    never lingers into the saved row."""
    if self.counterparty_type == CounterpartyType.CLIENTE:
        if self.counterparty_client_id is None:
            raise ValueError("Selecciona el cliente contraparte.")
        self.counterparty_supplier_id = None
        self.counterparty_name = None
    elif self.counterparty_type == CounterpartyType.PROVEEDOR:
        if self.counterparty_supplier_id is None:
            raise ValueError("Selecciona el proveedor contraparte.")
        self.counterparty_client_id = None
        self.counterparty_name = None
    else:
        if not self.counterparty_name or not self.counterparty_name.strip():
            raise ValueError("Escribe el nombre de la contraparte.")
        self.counterparty_client_id = None
        self.counterparty_supplier_id = None
    return self

# Suppliers


class SupplierPaymentAccountInput(BaseModel):
    payment_method: SupplierPaymentMethod
    bank_name: str | None = Field(default=None, max_length=100)
    account_holder: str = Field(min_length=2, max_length=150)
    document: str | None = Field(default=None, max_length=20)
    account_number: str | None = Field(default=None, max_length=40)
    account_type: str | None = Field(default=None, max_length=30)
    currency: AccountCurrency
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=150)
    notes: str | None = None
    is_active: bool = True


class SupplierPaymentAccountRead(SupplierPaymentAccountInput):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime


class SupplierCreate(BaseModel):
    filial_id: uuid.UUID
    business_name: str = Field(min_length=2, max_length=150)
    trade_name: str | None = None
    rif: str = Field(min_length=3, max_length=20)
    supplier_type: SupplierType
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    payment_accounts: list[SupplierPaymentAccountInput] = Field(default_factory=list)


class SupplierUpdate(BaseModel):
    business_name: str | None = None
    trade_name: str | None = None
    supplier_type: SupplierType | None = None
    contact_person: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    status: SupplierStatus | None = None


class SupplierRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    business_name: str
    trade_name: str | None
    rif: str
    supplier_type: SupplierType
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None
    status: SupplierStatus
    created_at: datetime


class SupplierDetailRead(SupplierRead):
    payment_accounts: list[SupplierPaymentAccountRead]
    purchase_history: list["PurchaseRequestRead"]


# Purchase requests


class PurchaseRequestLineInput(BaseModel):
    part_id: uuid.UUID
    quantity: int = Field(ge=1)


class PurchaseRequestCreate(BaseModel):
    filial_id: uuid.UUID
    supplier_id: uuid.UUID
    lines: list[PurchaseRequestLineInput] = Field(min_length=1)


class QuoteLineInput(BaseModel):
    line_id: uuid.UUID
    unit_cost: float = Field(ge=0)


class PurchaseRequestStatusUpdate(BaseModel):
    status: PurchaseRequestStatus
    quotes: list[QuoteLineInput] | None = None  # required moving to COTIZADA
    warehouse_id: uuid.UUID | None = None  # required moving to RECIBIDA
    # Optional — the shelf/bin location applied to every lot this receipt
    # creates. Omitted, a lot is received with no location (same as today).
    location: str | None = Field(default=None, max_length=30)


class PurchaseRequestLineRead(BaseModel):
    id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    unit_cost: float | None
    subtotal: float | None


class PurchaseRequestRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    supplier_id: uuid.UUID
    status: PurchaseRequestStatus
    lines: list[PurchaseRequestLineRead]
    total_quoted: float | None
    created_at: datetime
    updated_at: datetime


# Supplier claims


class SupplierClaimCreate(BaseModel):
    filial_id: uuid.UUID
    part_id: uuid.UUID
    quantity: int = Field(ge=1)
    supplier_id: uuid.UUID
    note: str | None = None
    claimed_amount: float | None = Field(default=None, ge=0)
    currency: AccountCurrency | None = None
    client_id: uuid.UUID | None = None
    # Set when this claim is generated automatically from a rework claim on
    # a defective part — see ServiceOrderService.create_rework_claim.
    lot_id: uuid.UUID | None = None
    purchase_request_id: uuid.UUID | None = None
    rework_claim_id: uuid.UUID | None = None


class SupplierClaimUpdate(BaseModel):
    status: ClaimStatus | None = None
    return_reference: str | None = None
    claimed_amount: float | None = None
    currency: AccountCurrency | None = None
    client_id: uuid.UUID | None = None


class SupplierClaimResolveInput(BaseModel):
    resolution: ClaimResolution
    note: str | None = None
    account_id: uuid.UUID | None = None
    client_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _check_resolution_requirements(self) -> "SupplierClaimResolveInput":
        if self.resolution == ClaimResolution.COSTO_TALLER and self.account_id is None:
            raise ValueError("account_id es requerido cuando la resolución es costo_taller.")
        return self


class SupplierClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    part_id: uuid.UUID
    quantity: int
    supplier_id: uuid.UUID
    status: ClaimStatus
    return_reference: str | None
    note: str | None
    created_at: datetime
    claimed_amount: float | None
    currency: AccountCurrency | None
    client_id: uuid.UUID | None
    client_name: str | None = None
    resolution: ClaimResolution | None
    resolution_note: str | None
    resolved_by_user_id: uuid.UUID | None
    resolved_at: datetime | None
    expense_entry_id: uuid.UUID | None
    lot_id: uuid.UUID | None = None
    purchase_request_id: uuid.UUID | None = None
    rework_claim_id: uuid.UUID | None = None


# Accounts


class AccountCreate(BaseModel):
    filial_id: uuid.UUID
    name: str = Field(min_length=2, max_length=100)
    bank: str | None = None
    currency: AccountCurrency
    account_type: AccountType
    opening_balance: float = 0


class AccountUpdate(BaseModel):
    name: str | None = None
    bank: str | None = None
    is_active: bool | None = None


class AccountRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    name: str
    bank: str | None
    currency: AccountCurrency
    account_type: AccountType
    opening_balance: float
    is_active: bool
    balance: float
    balance_usd: float
    created_at: datetime


# Income / Expense


class IncomeEntryCreate(BaseModel):
    filial_id: uuid.UUID
    entry_date: date
    concept: IncomeConcept
    description: str = Field(min_length=2, max_length=200)
    amount: float = Field(gt=0)
    currency: AccountCurrency
    account_id: uuid.UUID
    counterparty_type: CounterpartyType
    counterparty_client_id: uuid.UUID | None = None
    counterparty_supplier_id: uuid.UUID | None = None
    counterparty_name: str | None = Field(default=None, max_length=150)
    reference: str | None = Field(default=None, max_length=60)

    _validate_counterparty = model_validator(mode="after")(_validate_counterparty)


class IncomeEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    entry_date: date
    source: IncomeSource
    origin_reference: str | None
    concept: IncomeConcept | None
    description: str
    amount: float
    currency: AccountCurrency
    account_id: uuid.UUID
    counterparty_type: CounterpartyType | None
    counterparty_client_id: uuid.UUID | None
    counterparty_supplier_id: uuid.UUID | None
    counterparty_name: str | None
    reference: str | None
    exchange_rate: float | None
    amount_usd: float | None
    amount_bs: float | None
    attachment_url: str | None
    reverses_entry_id: uuid.UUID | None
    source_type: MovementSourceType | None
    source_id: uuid.UUID | None
    registered_by_user_id: uuid.UUID | None
    created_at: datetime


class ExpenseEntryCreate(BaseModel):
    filial_id: uuid.UUID
    entry_date: date
    category: ExpenseCategory
    beneficiary: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=2, max_length=200)
    amount: float = Field(gt=0)
    currency: AccountCurrency
    account_id: uuid.UUID
    counterparty_type: CounterpartyType
    counterparty_client_id: uuid.UUID | None = None
    counterparty_supplier_id: uuid.UUID | None = None
    counterparty_name: str | None = Field(default=None, max_length=150)
    reference: str | None = Field(default=None, max_length=60)

    _validate_counterparty = model_validator(mode="after")(_validate_counterparty)


class ExpenseEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    entry_date: date
    category: ExpenseCategory
    beneficiary: str
    description: str
    amount: float
    currency: AccountCurrency
    account_id: uuid.UUID
    counterparty_type: CounterpartyType | None
    counterparty_client_id: uuid.UUID | None
    counterparty_supplier_id: uuid.UUID | None
    counterparty_name: str | None
    reference: str | None
    exchange_rate: float | None
    amount_usd: float | None
    amount_bs: float | None
    attachment_url: str | None
    reverses_entry_id: uuid.UUID | None
    source_type: MovementSourceType | None
    source_id: uuid.UUID | None
    registered_by_user_id: uuid.UUID | None
    created_at: datetime


class AccountMovementRead(BaseModel):
    """Ingresos and Egresos normalized into one shape so an account's detail
    screen can render them as a single, chronologically merged feed."""

    id: uuid.UUID
    movement_type: str  # "ingreso" | "egreso"
    entry_date: date
    description: str
    amount: float
    currency: AccountCurrency
    concept: IncomeConcept | None = None
    category: ExpenseCategory | None = None
    counterparty_type: CounterpartyType | None
    counterparty_client_id: uuid.UUID | None
    counterparty_supplier_id: uuid.UUID | None
    counterparty_name: str | None
    reference: str | None
    attachment_url: str | None
    reverses_entry_id: uuid.UUID | None
    source_type: MovementSourceType | None
    source_id: uuid.UUID | None
    created_at: datetime


# Warranty submissions (monthly presentación to the holding)


class WarrantySubmissionCreate(BaseModel):
    filial_id: uuid.UUID
    period_year: int = Field(ge=2000, le=2100)
    period_month: int = Field(ge=1, le=12)
    currency: AccountCurrency


class WarrantySubmissionPayInput(BaseModel):
    account_id: uuid.UUID
    withholding_amount: float = Field(ge=0)
    net_amount_received: float = Field(ge=0)


class WarrantySubmissionClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    part_id: uuid.UUID
    supplier_id: uuid.UUID
    quantity: int
    claimed_amount: float | None
    currency: AccountCurrency | None
    resolution_note: str | None
    resolved_at: datetime | None


class WarrantySubmissionRead(BaseModel):
    id: uuid.UUID
    filial_id: uuid.UUID
    code: str
    period_year: int
    period_month: int
    currency: AccountCurrency
    status: WarrantySubmissionStatus
    claims: list[WarrantySubmissionClaimRead]
    total_claimed_amount: float
    submitted_at: datetime | None
    submitted_by_user_id: uuid.UUID | None
    withholding_amount: float | None
    net_amount_received: float | None
    account_id: uuid.UUID | None
    income_entry_id: uuid.UUID | None
    paid_at: datetime | None
    paid_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


# Reports


class MonthTrend(BaseModel):
    label: str
    income: float
    expense: float


class FinanceDashboard(BaseModel):
    income_month: float
    expense_month: float
    net_flow: float
    bcv_rate: float
    bcv_rate_is_stale: bool
    trend: list[MonthTrend]


class ProfitabilityDepartmentRow(BaseModel):
    key: str
    label: str
    net_sales: float
    direct_cost: float
    gross_profit: float
    margin: float


class ProfitabilityAdjustmentRow(BaseModel):
    key: str
    label: str
    origin: str
    amount: float  # signed — already the effect on net profit


class ProfitabilityReport(BaseModel):
    period_label: str
    filial_id: uuid.UUID | None  # null = consolidated across the holding
    date_from: date
    date_to: date
    bcv_rate: float
    bcv_rate_date: date | None
    period_is_closed: bool
    departments: list[ProfitabilityDepartmentRow]
    net_sales_total: float
    direct_cost_total: float
    gross_profit_total: float
    gross_margin: float
    adjustments: list[ProfitabilityAdjustmentRow]
    net_profit: float
    net_margin: float
    vehicles_sold_count: int
    vehicles_with_estimated_cost_count: int
    manual_movements_rate: float
    manual_movements_count: int
    manual_movements_total_count: int
