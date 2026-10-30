import uuid
from datetime import datetime, date
from sqlalchemy import DateTime, Date, ForeignKey, Integer, String, Text, Numeric, JSON, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class VehicleOpportunity(Base):
    __tablename__ = "vehicle_opportunities"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), index=True
    )
    client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_name: Mapped[str] = mapped_column(String(150))
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dealership_vehicles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    advisor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sale_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicle_sales.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    workflow_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default="0", nullable=False
    )
    workflow: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)
    pdi_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_orders.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    pdi_order = relationship("ServiceOrder", foreign_keys=[pdi_order_id], lazy="selectin")
    pdi_tasks = relationship(
        "ServiceOrderTask",
        primaryjoin="foreign(ServiceOrderTask.service_order_id) == VehicleOpportunity.pdi_order_id",
        viewonly=True,
        lazy="selectin",
    )
    origin: Mapped[str] = mapped_column(
        String(30), default="registro_directo", server_default="registro_directo"
    )
    stage: Mapped[str] = mapped_column(String(25), default="prospecto", server_default="prospecto")
    vehicle_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quoted_amount: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    quoted_currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    sale_type: Mapped[str] = mapped_column(String(15), default="contado", server_default="contado")
    quote_expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    discount_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0, server_default="0"
    )
    quoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_contact_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    lost_reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    activity: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    client = relationship("Client", foreign_keys=[client_id])
    vehicle = relationship("DealershipVehicle", foreign_keys=[vehicle_id])
    advisor = relationship("User", foreign_keys=[advisor_user_id])
    sale = relationship("VehicleSale", foreign_keys=[sale_id])


class ReservationSettlement(Base):
    __tablename__ = "vehicle_reservation_settlements"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicle_opportunities.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filiales.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    deposit_income_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("income_entries.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    refund_expense_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("expense_entries.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )
    retained_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    refund_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    retention_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
