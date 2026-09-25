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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.post_ventas.enums import (
    CATEGORY_PREFIXES,
    TemparioCategory,
    VehicleWarrantySource,
    WarrantyPolicyAppliesTo,
    WarrantyPolicyCoveredBy,
    WarrantyPolicyScope,
    WarrantyPolicyStatus,
    WorkshopWarrantyCoverage,
)


class LaborSettings(Base):
    """Filial-wide labor rate and billing settings. One row per filial —
    changing it re-prices every tempario and open service order automatically,
    since prices are always calculated live, never stored."""

    __tablename__ = "labor_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filiales.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    hourly_rate: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=25)
    commission_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=30)
    igtf_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=3)
    iva_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=16, server_default="16"
    )
    bcv_rate: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    bcv_rate_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Default IVA/ISLR withholding percentages applied when billing a
    # contribuyente especial (typically an empresa-type client) — 0 until
    # the filial's accounting team configures the actual figures that apply
    # to their classification; always overridable per invoice.
    iva_retention_default_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0, server_default="0"
    )
    islr_retention_default_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0, server_default="0"
    )
    # Warranty term for parts sold at the counter (not installed by the
    # workshop) — a separate term from any workshop/labor warranty, since a
    # shelved part ages differently from a fresh install.
    part_warranty_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default="90"
    )
    # Default factory-warranty term (months) applied when a vehicle sale
    # auto-creates its VehicleWarranty and no more specific term was given.
    # A manual/bulk warranty entry can always override this per vehicle.
    vehicle_warranty_default_months: Mapped[int] = mapped_column(
        Integer, nullable=False, default=36, server_default="36"
    )
    # Labor/installation term of the shop's own workshop warranty — a
    # botched install tends to fail within days, so this is usually the
    # shorter of the two. Separate from part_warranty_days (counter sales)
    # and vehicle_warranty_default_months (factory warranty).
    workshop_warranty_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default="90"
    )
    workshop_warranty_km: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5000, server_default="5000"
    )
    # Parts-installed-by-the-shop term — a part wears out on a slower clock
    # than a bad install fails, hence the separate term. Kept independently
    # addressable even though, today, the Ajustes screen only exposes one
    # "garantía del taller" input and mirrors that value into both pairs.
    workshop_parts_warranty_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default="90"
    )
    workshop_parts_warranty_km: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5000, server_default="5000"
    )
    # Manual Ingresos/Egresos movements at or above this USD-equivalent
    # amount require an attached supporting document.
    manual_movement_attachment_threshold_usd: Mapped[float] = mapped_column(
        Numeric(10, 2), nullable=False, default=100, server_default="100"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def bcv_rate_is_stale(self) -> bool:
        """True once the stored rate isn't today's — mirrors billing.py's
        same-day requirement for charging in Bs."""
        return self.bcv_rate_date is None or self.bcv_rate_date < date.today()


class Tempario(Base):
    """A catalog service (task) with its standard time, compatibility, tools
    and parts. Its price is computed on read, not stored."""

    __tablename__ = "temparios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[TemparioCategory] = mapped_column(
        Enum(TemparioCategory, name="tempario_category"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    estimated_hours: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    year_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compatible_vehicles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    requires_parts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    parts: Mapped[list["TemparioPart"]] = relationship(
        back_populates="tempario", cascade="all, delete-orphan"
    )

    @property
    def code(self) -> str:
        return f"{CATEGORY_PREFIXES[self.category]}-{self.sequence_number}"


class MaintenancePlan(Base):
    """A brand's manufacturer maintenance schedule (like MPT) — which
    Temparios apply and at what km/month interval. Lives next to Tempario
    because a plan entry is just a Tempario (already carrying its own
    parts via TemparioPart) plus a schedule; it doesn't redefine work or
    parts on its own."""

    __tablename__ = "maintenance_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    brand: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    entries: Mapped[list["MaintenancePlanEntry"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )


class MaintenancePlanEntry(Base):
    __tablename__ = "maintenance_plan_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("maintenance_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tempario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("temparios.id", ondelete="RESTRICT"), nullable=False
    )
    interval_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    interval_months: Mapped[int | None] = mapped_column(Integer, nullable=True)

    plan: Mapped["MaintenancePlan"] = relationship(back_populates="entries")
    tempario: Mapped["Tempario"] = relationship(lazy="selectin")


class TemparioPart(Base):
    """A part consumed by a tempario, with its cost — used to compute the
    parts-cost + 30% margin portion of the tempario's total price."""

    __tablename__ = "tempario_parts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tempario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("temparios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_cost: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    tempario: Mapped["Tempario"] = relationship(back_populates="parts")


class VehicleWarranty(Base):
    """A factory (manufacturer) warranty on a specific vehicle, identified by
    VIN — not by a FK to clients.Vehicle or concesionario.DealershipVehicle,
    since a warranty can exist before either of those rows does (a workshop
    servicing other brands routinely registers a warranty for a car it never
    sold and that has no service history here yet). Created automatically
    when a DealershipVehicle sale closes, or entered by hand/in bulk."""

    __tablename__ = "vehicle_warranties"
    __table_args__ = (UniqueConstraint("filial_id", "vin", name="uq_vehicle_warranty_filial_vin"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vin: Mapped[str] = mapped_column(String(17), nullable=False)
    brand: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    starts_at: Mapped[date] = mapped_column(Date, nullable=False)
    duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Computed once from starts_at + duration_months at creation time (not
    # live) — km-based coverage has no expiration date, it's just shown as
    # informational text since tracking it live would need an odometer
    # reading tied to a VIN that may not have any Vehicle record at all.
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[VehicleWarrantySource] = mapped_column(
        Enum(VehicleWarrantySource, name="vehicle_warranty_source"), nullable=False
    )
    dealership_vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dealership_vehicles.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkshopWarranty(Base):
    """The shop's own warranty on a piece of work it did — one row per
    (ServiceOrderTask, coverage_type), created automatically the moment its
    order is invoiced (BillingService.issue). A task always gets a
    MANO_DE_OBRA row; it additionally gets a REPUESTO row if that task
    installed a part — an install fails fast, a part wears out slowly, so
    each runs on its own term. VIN-anchored like VehicleWarranty, not FK'd
    to Client, so it survives the vehicle changing hands."""

    __tablename__ = "workshop_warranties"
    __table_args__ = (
        UniqueConstraint(
            "service_order_task_id", "coverage_type", name="uq_workshop_warranty_task_coverage"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vin: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    service_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_orders.id", ondelete="RESTRICT"), nullable=False
    )
    service_order_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("service_order_tasks.id", ondelete="RESTRICT"), nullable=False
    )
    coverage_type: Mapped[WorkshopWarrantyCoverage] = mapped_column(
        Enum(WorkshopWarrantyCoverage, name="workshop_warranty_coverage"),
        nullable=False,
        default=WorkshopWarrantyCoverage.MANO_DE_OBRA,
    )
    # Snapshotted off the task, same reasoning as ServiceOrderTask's own
    # snapshot fields — display never needs to join the tempario catalog.
    tempario_code_snapshot: Mapped[str] = mapped_column(String(20), nullable=False)
    tempario_name_snapshot: Mapped[str] = mapped_column(String(150), nullable=False)
    technician_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    starts_at: Mapped[date] = mapped_column(Date, nullable=False)
    # The settings/policy values actually used, snapshotted so a later
    # Ajustes change or WarrantyPolicy edit never retroactively rewrites an
    # already-issued warranty. Null when the policy selected (if any) has
    # no_expiration=True — a campaign-style warranty with no cutoff.
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Null only when the order never recorded an intake mileage, or the
    # applicable duration_km is itself null (no-expiration policy).
    expiration_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Traceability only, never re-read for terms (those are snapshotted
    # above) — set only when a WarrantyPolicy was selected on the ODS;
    # null means this warranty used the filial's LaborSettings fallback.
    warranty_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warranty_policies.id", ondelete="SET NULL"), nullable=True
    )
    warranty_policy_name_snapshot: Mapped[str | None] = mapped_column(String(150), nullable=True)
    covered_by_snapshot: Mapped[WarrantyPolicyCoveredBy | None] = mapped_column(
        Enum(WarrantyPolicyCoveredBy, name="warranty_policy_covered_by"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WarrantyPolicy(Base):
    """A named, reusable warranty policy an advisor selects on an ODS (one
    for labor, one for parts) — replaces the old implicit "every order gets
    the filial's LaborSettings term" behavior with an explicit, catalogued
    choice. Never hard-deleted: `status` is toggled to INACTIVA instead,
    since issued WorkshopWarranty rows may still reference it (see
    `warranty_policy_id` there) even after it's retired."""

    __tablename__ = "warranty_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    applies_to: Mapped[WarrantyPolicyAppliesTo] = mapped_column(
        Enum(WarrantyPolicyAppliesTo, name="warranty_policy_applies_to"), nullable=False
    )
    covered_by: Mapped[WarrantyPolicyCoveredBy] = mapped_column(
        Enum(WarrantyPolicyCoveredBy, name="warranty_policy_covered_by"), nullable=False
    )
    scope: Mapped[WarrantyPolicyScope] = mapped_column(
        Enum(WarrantyPolicyScope, name="warranty_policy_scope"), nullable=False
    )
    # Vigencia: either "sin vencimiento" (campaigns — both durations null)
    # or at least one of duration_days/duration_km, whichever comes first —
    # enforced by the create/update schema, not here.
    no_expiration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_km: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[WarrantyPolicyStatus] = mapped_column(
        Enum(WarrantyPolicyStatus, name="warranty_policy_status"),
        nullable=False,
        default=WarrantyPolicyStatus.ACTIVA,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    temparios: Mapped[list["WarrantyPolicyTempario"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )
    parts: Mapped[list["WarrantyPolicyPart"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )


class WarrantyPolicyTempario(Base):
    """Informational link: which temparios this policy is documented to
    cover — shown on the policy's detail screen, does not restrict which
    policies an ODS can select (confirmed with the user)."""

    __tablename__ = "warranty_policy_temparios"
    __table_args__ = (
        UniqueConstraint("policy_id", "tempario_id", name="uq_warranty_policy_tempario"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warranty_policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tempario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("temparios.id", ondelete="RESTRICT"), nullable=False
    )

    policy: Mapped["WarrantyPolicy"] = relationship(back_populates="temparios")
    tempario: Mapped["Tempario"] = relationship(lazy="selectin")


class WarrantyPolicyPart(Base):
    """Informational link: which parts this policy is documented to cover —
    same reasoning as WarrantyPolicyTempario."""

    __tablename__ = "warranty_policy_parts"
    __table_args__ = (UniqueConstraint("policy_id", "part_id", name="uq_warranty_policy_part"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("warranty_policies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), nullable=False
    )

    policy: Mapped["WarrantyPolicy"] = relationship(back_populates="parts")
    part: Mapped["Part"] = relationship(lazy="selectin")