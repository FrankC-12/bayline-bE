import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
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


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    business_name: Mapped[str] = mapped_column(String(150), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    rif: Mapped[str] = mapped_column(String(20), nullable=False)
    supplier_type: Mapped[SupplierType] = mapped_column(
        Enum(SupplierType, name="supplier_type"), nullable=False
    )
    contact_person: Mapped[str | None] = mapped_column(String(150), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[SupplierStatus] = mapped_column(
        Enum(SupplierStatus, name="supplier_status"), nullable=False, default=SupplierStatus.ACTIVO
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    payment_accounts: Mapped[list["SupplierPaymentAccount"]] = relationship(
        back_populates="supplier", cascade="all, delete-orphan"
    )


class SupplierPaymentAccount(Base):
    __tablename__ = "supplier_payment_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_method: Mapped[SupplierPaymentMethod] = mapped_column(
        Enum(SupplierPaymentMethod, name="supplier_payment_method"), nullable=False
    )
    bank_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    account_holder: Mapped[str] = mapped_column(String(150), nullable=False)
    document: Mapped[str | None] = mapped_column(String(20), nullable=True)
    account_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    account_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    currency: Mapped[AccountCurrency] = mapped_column(
        Enum(AccountCurrency, name="account_currency", create_type=False), nullable=False
    )
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    supplier: Mapped["Supplier"] = relationship(back_populates="payment_accounts")


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[PurchaseRequestStatus] = mapped_column(
        Enum(PurchaseRequestStatus, name="purchase_request_status"),
        nullable=False,
        default=PurchaseRequestStatus.ENVIADA,
    )
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["PurchaseRequestLine"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )

    @property
    def code(self) -> str:
        return f"SC-{self.sequence_number}"


class PurchaseRequestLine(Base):
    __tablename__ = "purchase_request_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    request: Mapped["PurchaseRequest"] = relationship(back_populates="lines")


class SupplierClaim(Base):
    __tablename__ = "supplier_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus, name="claim_status"), nullable=False, default=ClaimStatus.PENDIENTE_ENVIO
    )
    return_reference: Mapped[str | None] = mapped_column(String(30), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    claimed_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[AccountCurrency | None] = mapped_column(
        Enum(AccountCurrency, name="account_currency", create_type=False), nullable=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )

    # Populated only once the importador rejects the claim and someone decides
    # who eats the cost — see AdministracionService.resolve_claim.
    resolution: Mapped[ClaimResolution | None] = mapped_column(
        Enum(ClaimResolution, name="claim_resolution"), nullable=True
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expense_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_entries.id", ondelete="SET NULL"), nullable=True
    )

    # Set once this claim's workshop-absorbed cost is bundled into a monthly
    # submission to the holding for reimbursement — see WarrantySubmission.
    warranty_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warranty_submissions.id", ondelete="SET NULL"), nullable=True
    )
    warranty_submission: Mapped["WarrantySubmission | None"] = relationship(back_populates="claims")

    # Populated when this claim was generated automatically from a warranty
    # claim on a defective part (claim_type=repuesto_proveedor) — traces
    # part -> lot -> purchase order -> supplier without anyone having to
    # look it up. Null for a manually created claim (e.g. a defect found at
    # the counter, unrelated to any warranty claim).
    lot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("part_lots.id", ondelete="SET NULL"), nullable=True
    )
    purchase_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requests.id", ondelete="SET NULL"), nullable=True
    )
    warranty_claim_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warranty_claims.id", ondelete="SET NULL"), nullable=True
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    bank: Mapped[str | None] = mapped_column(String(80), nullable=True)
    currency: Mapped[AccountCurrency] = mapped_column(
        Enum(AccountCurrency, name="account_currency"), nullable=False
    )
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type"), nullable=False
    )
    # Balance the account carried in before Bayline started tracking its
    # movements — added once at creation, not itself an Income/Expense
    # entry. The account's balance is this plus every entry posted to it.
    opening_balance: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IncomeEntry(Base):
    __tablename__ = "income_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[IncomeSource] = mapped_column(
        Enum(IncomeSource, name="income_source"), nullable=False, default=IncomeSource.MANUAL
    )
    origin_reference: Mapped[str | None] = mapped_column(String(30), nullable=True)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[AccountCurrency] = mapped_column(
        Enum(AccountCurrency, name="account_currency"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # Everything below is nullable — rows created by the automatic paths
    # (record_automatic_income, mark_warranty_submission_paid, billing.py's
    # issue/collect_invoice) never set these; only the manual-entry form
    # (AdministracionService.create_income) does, via required Pydantic
    # fields on IncomeEntryCreate. A non-null concept/counterparty_type is
    # exactly what distinguishes a "genuinely manual" row for the KPI.
    concept: Mapped[IncomeConcept | None] = mapped_column(
        Enum(IncomeConcept, name="income_concept"), nullable=True
    )
    counterparty_type: Mapped[CounterpartyType | None] = mapped_column(
        Enum(CounterpartyType, name="counterparty_type"), nullable=True
    )
    counterparty_client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    counterparty_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    counterparty_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # BCV USD/Bs rate frozen at entry_date, and both currency equivalents —
    # so the movement's value never depends on re-reading a rate that moves.
    exchange_rate: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    amount_usd: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # A manual entry is never edited/deleted — the only correction is a new,
    # equal-and-opposite entry dated today referencing the original here.
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("income_entries.id", ondelete="SET NULL"), nullable=True
    )
    # Polymorphic pointer back at whatever document generated this entry
    # (a VehicleSale, PartSale, ServiceOrder...) — no FK constraint since the
    # target table varies with source_type; lets the account-detail screen
    # open the real invoice instead of just showing a description string.
    source_type: Mapped[MovementSourceType | None] = mapped_column(
        Enum(MovementSourceType, name="movement_source_type"), nullable=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    registered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WarrantySubmission(Base):
    """The monthly bundle of workshop-absorbed warranty costs (rejected
    SupplierClaims resolved as COSTO_TALLER) sent up to the holding for
    reimbursement. borrador -> presentada -> pagada."""

    __tablename__ = "warranty_submissions"
    __table_args__ = (
        UniqueConstraint("filial_id", "period_year", "period_month", "currency", name="uq_warranty_submission_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[AccountCurrency] = mapped_column(
        Enum(AccountCurrency, name="account_currency", create_type=False), nullable=False
    )
    status: Mapped[WarrantySubmissionStatus] = mapped_column(
        Enum(WarrantySubmissionStatus, name="warranty_submission_status"),
        nullable=False,
        default=WarrantySubmissionStatus.BORRADOR,
    )

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    withholding_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    net_amount_received: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    income_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("income_entries.id", ondelete="SET NULL"), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    claims: Mapped[list["SupplierClaim"]] = relationship(back_populates="warranty_submission")

    @property
    def code(self) -> str:
        return f"PG-{self.sequence_number}"


class ExpenseEntry(Base):
    __tablename__ = "expense_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[ExpenseCategory] = mapped_column(
        Enum(ExpenseCategory, name="expense_category"), nullable=False
    )
    beneficiary: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[AccountCurrency] = mapped_column(
        Enum(AccountCurrency, name="account_currency"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    # Same nullable-everywhere reasoning as IncomeEntry — see that model's
    # comment. category already serves as this table's "concepto" (closed
    # list), so there's no separate concept column here.
    counterparty_type: Mapped[CounterpartyType | None] = mapped_column(
        Enum(CounterpartyType, name="counterparty_type"), nullable=True
    )
    counterparty_client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    counterparty_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True
    )
    counterparty_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(60), nullable=True)
    exchange_rate: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    amount_usd: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount_bs: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_entries.id", ondelete="SET NULL"), nullable=True
    )
    # See IncomeEntry.source_type/source_id — same polymorphic, no-FK link
    # back at whatever generated this expense (e.g. a resolved SupplierClaim).
    source_type: Mapped[MovementSourceType | None] = mapped_column(
        Enum(MovementSourceType, name="movement_source_type", create_type=False), nullable=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    registered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
