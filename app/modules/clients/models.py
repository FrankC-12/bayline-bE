import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.clients.enums import (
    AddressType,
    ClientType,
    ContactPreference,
    DocumentType,
    FuelType,
    TransmissionType,
)


class Client(Base):
    """A client (particular or company) that belongs to a single filial.
    Shared across Taller, Concesionario and Venta de Repuestos."""

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("filial_id", "document_number", name="uq_clients_filial_document"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    client_type: Mapped[ClientType] = mapped_column(Enum(ClientType, name="client_type"), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, name="document_type"), nullable=False
    )
    document_number: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_primary: Mapped[str] = mapped_column(String(20), nullable=False)
    phone_secondary: Mapped[str | None] = mapped_column(String(20), nullable=True)
    contact_preference: Mapped[ContactPreference | None] = mapped_column(
        Enum(ContactPreference, name="contact_preference"), nullable=True
    )
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    address_type: Mapped[AddressType | None] = mapped_column(
        Enum(AddressType, name="address_type"), nullable=True
    )
    # Marks the client record that stands in for the holding when factory-
    # warranty work gets billed to it instead of the vehicle's owner — lets
    # the holding-scoped consolidated report find the right invoices per
    # filial. At most one such client is expected per filial, but this isn't
    # enforced at the DB level (same as everything else about this client).
    is_holding_billing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Same idea, generalized to a Supplier: a client record that stands in
    # for a Supplier so an ODS can be billed to it (e.g. the manufacturer or
    # a parts supplier covering a warranty claim) using the Supplier's own
    # business_name/RIF, without inventing a parallel "who can be billed"
    # concept alongside Client. SET NULL rather than blocking supplier
    # deletion — the invoice's frozen document already has its own copy of
    # the name/RIF at issuance time.
    linked_supplier_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vehicles: Mapped[list["Vehicle"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class Vehicle(Base):
    """A vehicle owned by a client."""

    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    brand: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vin: Mapped[str | None] = mapped_column(String(17), nullable=True)
    mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchase_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    body_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    plate: Mapped[str | None] = mapped_column(String(8), nullable=True)
    color: Mapped[str | None] = mapped_column(String(40), nullable=True)
    upholstery: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fuel_type: Mapped[FuelType | None] = mapped_column(Enum(FuelType, name="fuel_type"), nullable=True)
    transmission: Mapped[TransmissionType | None] = mapped_column(
        Enum(TransmissionType, name="transmission_type"), nullable=True
    )
    # Advisor-entered "next visit suggested" date, set when closing a Service
    # Order — drives the "mantenimiento por vencer" list in Torre de Control.
    # Not auto-computed from any fixed interval: each closed order can suggest
    # its own next date, since what was serviced varies.
    next_maintenance_due_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The specific plan task (Tempario) the advisor flagged as pending for
    # this vehicle's next visit — lets a new ODS offer to load that exact
    # task (and its linked parts) instead of searching the catalog again.
    # SET NULL (not RESTRICT like ServiceOrderTask.tempario_id) because this
    # is just a live suggestion, not billed history.
    next_maintenance_tempario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("temparios.id", ondelete="SET NULL"), nullable=True
    )
    # The manufacturer maintenance plan (MPT) this vehicle follows — assigned
    # explicitly (suggested by brand match, confirmed by the advisor), never
    # auto-assigned, since a vehicle could legitimately follow a different
    # plan than its brand's default. SET NULL since it's a live pointer, not
    # billed history.
    maintenance_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("maintenance_plans.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client: Mapped["Client"] = relationship(back_populates="vehicles")