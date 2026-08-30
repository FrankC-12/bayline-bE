import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.administracion.enums import (
    AccountCurrency,
    AccountType,
    ClaimStatus,
    ExpenseCategory,
    IncomeSource,
    PurchaseRequestStatus,
    SupplierStatus,
    SupplierType,
)

# Suppliers


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


class SupplierClaimUpdate(BaseModel):
    status: ClaimStatus | None = None
    return_reference: str | None = None


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


# Accounts


class AccountCreate(BaseModel):
    filial_id: uuid.UUID
    name: str = Field(min_length=2, max_length=100)
    bank: str | None = None
    currency: AccountCurrency
    account_type: AccountType


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
    is_active: bool
    balance: float
    balance_usd: float
    created_at: datetime


# Income / Expense


class IncomeEntryCreate(BaseModel):
    filial_id: uuid.UUID
    entry_date: date
    description: str = Field(min_length=2, max_length=200)
    amount: float = Field(ge=0)
    currency: AccountCurrency
    account_id: uuid.UUID
    source: IncomeSource = IncomeSource.MANUAL
    origin_reference: str | None = None


class IncomeEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filial_id: uuid.UUID
    entry_date: date
    source: IncomeSource
    origin_reference: str | None
    description: str
    amount: float
    currency: AccountCurrency
    account_id: uuid.UUID
    registered_by_user_id: uuid.UUID | None
    created_at: datetime


class ExpenseEntryCreate(BaseModel):
    filial_id: uuid.UUID
    entry_date: date
    category: ExpenseCategory
    beneficiary: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=2, max_length=200)
    amount: float = Field(ge=0)
    currency: AccountCurrency
    account_id: uuid.UUID


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
    registered_by_user_id: uuid.UUID | None
    created_at: datetime


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
    trend: list[MonthTrend]


class ProfitabilityReport(BaseModel):
    period_label: str
    total_income: float
    parts_cost: float
    vehicles_cost: float
    gross_profit: float
    operating_expenses: float
    commissions_paid: float
    shrinkage_losses: float
    net_profit: float