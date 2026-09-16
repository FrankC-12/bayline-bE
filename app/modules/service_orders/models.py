import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.service_orders.enums import (
    ReworkAuthorizationStatus,
    ReworkClaimStatus,
    ReworkFailureCategory,
    ServiceOrderPayer,
    ServiceOrderStatus,
    ServiceOrderType,
    TaskStatus,
    TransferStatus,
    UpsellStatus,
    WarrantyClaimStatus,
)


class Bay(Base):
    """A physical service bay within a filial's workshop."""

    __tablename__ = "bays"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filiales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceOrder(Base):
    """A work order (ODS) tracking a vehicle through the workshop."""

    __tablename__ = "service_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filiales.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[ServiceOrderStatus] = mapped_column(
        Enum(ServiceOrderStatus, name="service_order_status"),
        nullable=False,
        default=ServiceOrderStatus.PENDIENTE,
    )
    order_type: Mapped[ServiceOrderType] = mapped_column(
        Enum(ServiceOrderType, name="service_order_type"),
        nullable=False,
        default=ServiceOrderType.REGULAR,
    )
    technician_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    advisor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    bay_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bays.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    intake_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    customer_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    promised_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discount_label: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="Costo + 30% (Sin Descuento)",
        server_default="Costo + 30% (Sin Descuento)",
    )
    invoiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pricing_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    @property
    def code(self) -> str:
        """Human-facing code, e.g. 'ODS-2041'. Computed, not stored."""
        return f"ODS-{self.sequence_number}"


class ServiceOrderTask(Base):
    """A tempario applied to a service order — the 'Tareas a realizar' rows.
    Snapshots the tempario's code/name/hours at the time it was added, so
    later changes to the tempario catalog don't retroactively change history."""

    __tablename__ = "service_order_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tempario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("temparios.id", ondelete="RESTRICT"), nullable=False
    )
    code_snapshot: Mapped[str] = mapped_column(String(20), nullable=False)
    name_snapshot: Mapped[str] = mapped_column(String(150), nullable=False)
    hours_snapshot: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status"), nullable=False, default=TaskStatus.PENDIENTE
    )
    payer: Mapped[ServiceOrderPayer] = mapped_column(
        Enum(ServiceOrderPayer, name="service_order_payer"),
        nullable=False,
        default=ServiceOrderPayer.CLIENTE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ServiceOrderTransfer(Base):
    """An 'Orden de Transferencia' (ODT) — a batch of parts requested from the
    warehouse for a service order. Stock is only decremented when marked as
    'Pedido', not when a line is added, so it can still be adjusted freely."""

    __tablename__ = "service_order_transfers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[TransferStatus] = mapped_column(
        Enum(TransferStatus, name="transfer_status"),
        nullable=False,
        default=TransferStatus.PENDIENTE,
    )
    fulfilled_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Whether almacén staff has acknowledged this dispatched request in the
    # "Órdenes de Transferencia" screen — drives the unseen-count badge there.
    warehouse_seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["ServiceOrderTransferLine"]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan"
    )

    @property
    def code(self) -> str:
        return f"ODT{self.sequence_number}"


class ServiceOrderTransferLine(Base):
    __tablename__ = "service_order_transfer_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_order_transfers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    cost_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    line_total: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, default=0)
    payer: Mapped[ServiceOrderPayer] = mapped_column(
        Enum(ServiceOrderPayer, name="service_order_payer"),
        nullable=False,
        default=ServiceOrderPayer.CLIENTE,
    )
    # Which task (if any) caused this line to be added — null means it was
    # added directly to the order, not auto-linked from a tempario's catalog
    # parts. This is what lets billing decide whether a given task installed
    # a part at all (and therefore needs its own "repuesto" warranty term).
    service_order_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_order_tasks.id", ondelete="SET NULL"), nullable=True
    )

    transfer: Mapped["ServiceOrderTransfer"] = relationship(back_populates="lines")
    allocations: Mapped[list["ServiceOrderTransferLotAllocation"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class ServiceOrderTransferLotAllocation(Base):
    """Which FIFO lot(s) an ODT line's price is actually built from —
    mirrors parts.PartSaleLotAllocation, so a service order's part
    consumption can be traced to a lot (and from there to a purchase order
    and a supplier) the same way a counter sale already can. Set as a
    preview when the line is added (before dispatch, no stock is touched
    yet) and replaced with the authoritative allocation at dispatch time
    (ServiceOrderService.mark_transfer_ordered). Carries its own
    warehouse_id, unlike PartSaleLotAllocation — an ODT isn't scoped to one
    warehouse like a counter sale is, so a line can draw from lots in
    different warehouses."""

    __tablename__ = "service_order_transfer_lot_allocations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transfer_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_order_transfer_lines.id", ondelete="CASCADE"), index=True
    )
    lot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("part_lots.id", ondelete="RESTRICT")
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warehouses.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_cost: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)


class Upsell(Base):
    """Additional work a technician spots while working an ODS — a free-text
    finding, not tied to the Tempario catalog. The advisor approves, postpones
    or rejects it; approving does not automatically add it as a billable
    task (that's a separate, deliberate step from the ODS detail)."""

    __tablename__ = "upsells"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[UpsellStatus] = mapped_column(
        Enum(UpsellStatus, name="upsell_status"), nullable=False, default=UpsellStatus.PENDIENTE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ServiceOrderInvoice(Base):
    __tablename__ = "service_order_invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_orders.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    total_usd: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Who actually gets billed — normally the vehicle's owner, but a factory-
    # warranty order can be billed to a different Client (e.g. the holding,
    # registered as a regular empresa client). RESTRICT: a client with billed
    # invoices can't be deleted out from under them.
    billed_client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    # Only meaningful when billed_client_id differs from the vehicle's owner —
    # a simple record that the vehicle's actual owner signed off on the work,
    # not a real e-signature capture.
    client_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_confirmed_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # IVA/ISLR withheld by a contribuyente especial (typically an empresa
    # client) at the moment of billing — frozen here just like total_usd,
    # since the percentages are known when the invoice is issued, not
    # discovered later. Zero/null for a client that isn't a withholding
    # agent. These reduce what's actually expected in cash — see
    # amount_paid_at_issuance and collected_at below.
    iva_retention_percentage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    iva_retention_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    islr_retention_percentage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    islr_retention_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)

    # How much of total_usd was actually collected at issuance. Historically
    # this always equaled total_usd (payment was mandatory in full); now an
    # invoice can be issued with less collected up front, leaving a
    # receivable — see collected_at below.
    amount_paid_at_issuance: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)

    # Cuentas por cobrar: null while the rest of total_usd is still pending.
    # Set once the remainder gets collected (with its withholdings).
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withholding_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    net_collected_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    collection_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=True
    )
    collection_income_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("income_entries.id", ondelete="SET NULL"), nullable=True
    )
    collected_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ReworkClaim(Base):
    """A client's comeback complaint about an already-invoiced ServiceOrder —
    the shop's own labor/parts warranty. Distinct from VehicleWarranty (the
    manufacturer's factory warranty) and from SupplierClaim (a claim against
    a parts supplier) — this is what powers the Torre de Control rework-rate
    metric: of what we invoiced, how much came back, by whom, on what
    service, on what part, and how long after billing."""

    __tablename__ = "rework_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_orders.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Both optional and independent — a claim can point at a task, a part,
    # both, or neither (a vague "no arrancó" with no specific culprit yet).
    tempario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("temparios.id", ondelete="SET NULL"), nullable=True
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True
    )
    # Structured, unlike failure_cause (free text) — this is what drives the
    # automatic SupplierClaim when the part itself was at fault. Null while
    # the claim is still open — the cause may genuinely be unknown the day
    # the client complains, before anyone has diagnosed it. Required before
    # the claim can be closed (see status below) — that's the gate that
    # keeps the rework analytics trustworthy.
    failure_category: Mapped[ReworkFailureCategory | None] = mapped_column(
        Enum(ReworkFailureCategory, name="rework_failure_category"), nullable=True
    )
    failure_cause: Mapped[str] = mapped_column(String(300), nullable=False)
    claimed_at: Mapped[date] = mapped_column(Date, nullable=False)
    # Usually the advisor, not the technician who did the work — see the
    # module docstring/UI copy about this data-quality caveat.
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    status: Mapped[ReworkClaimStatus] = mapped_column(
        Enum(ReworkClaimStatus, name="rework_claim_status"), nullable=False, default=ReworkClaimStatus.ABIERTO
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # An advisor can't give away warranty work on their own — a manager
    # (gated by administracion-module access, not asesor-servicios — see
    # the router) must approve or reject before any redo work happens.
    authorization_status: Mapped[ReworkAuthorizationStatus] = mapped_column(
        Enum(ReworkAuthorizationStatus, name="rework_authorization_status"),
        nullable=False,
        default=ReworkAuthorizationStatus.PENDIENTE,
    )
    authorized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The explicit, recorded exception required to approve without a vigente
    # factory warranty on the vehicle.
    warranty_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    warranty_override_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The order opened as a consequence of the decision — "retrabajo" (free)
    # if approved, "regular" (billable) if rejected. Set exactly once.
    resulting_service_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_orders.id", ondelete="SET NULL"), nullable=True
    )


class WarrantyClaim(Base):
    """A customer's comeback complaint logged against the vehicle's factory
    warranty (VehicleWarranty), raised before any ServiceOrder exists. Stays
    'solicitado' until a manager authorizes it (future work) — creating this
    row never opens an order."""

    __tablename__ = "warranty_claims"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reported_symptom: Mapped[str] = mapped_column(Text, nullable=False)
    reported_mileage: Mapped[int] = mapped_column(Integer, nullable=False)
    # Snapshot of the vehicle's current_mileage at claim time — current_mileage
    # is a live derived value that will drift, so this preserves what was
    # actually compared against when the inconsistency flag was set.
    vehicle_mileage_at_claim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mileage_inconsistent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[WarrantyClaimStatus] = mapped_column(
        Enum(WarrantyClaimStatus, name="warranty_claim_status"),
        nullable=False,
        default=WarrantyClaimStatus.SOLICITADO,
    )
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    warranty_links: Mapped[list["WarrantyClaimWarranty"]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class WarrantyClaimWarranty(Base):
    """Join row: which of the vehicle's vigente factory warranties the
    advisor marked as applying to this claim. Plain many-to-many — a claim
    can cover more than one warranty line (e.g. separate powertrain + paint
    coverage on the same VIN)."""

    __tablename__ = "warranty_claim_warranties"
    __table_args__ = (
        UniqueConstraint("warranty_claim_id", "vehicle_warranty_id", name="uq_warranty_claim_warranty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    warranty_claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warranty_claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_warranty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_warranties.id", ondelete="RESTRICT"), nullable=False
    )

    claim: Mapped["WarrantyClaim"] = relationship(back_populates="warranty_links")
