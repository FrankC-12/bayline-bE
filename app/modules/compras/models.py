import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.compras.enums import VehiclePurchaseOrderStatus


class VehiclePurchaseOrder(Base):
    """A purchase order (OC) to an importer/brand for new vehicle stock —
    unrelated to PurchaseRequest (parts restocking). Vehicles ordered here
    arrive VIN-by-VIN across one or more receptions; margin at sale time is
    always specific-identification against that VIN's own cost (see
    DealershipVehicle.cost_price), never FIFO."""

    __tablename__ = "vehicle_purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supplier_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[VehiclePurchaseOrderStatus] = mapped_column(
        Enum(VehiclePurchaseOrderStatus, name="vehicle_purchase_order_status"),
        nullable=False,
        default=VehiclePurchaseOrderStatus.ENVIADA,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["VehiclePurchaseOrderLine"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan", order_by="VehiclePurchaseOrderLine.id"
    )
    receptions: Mapped[list["VehiclePurchaseOrderReception"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan", order_by="VehiclePurchaseOrderReception.received_at"
    )
    invoices: Mapped[list["VehiclePurchaseOrderInvoice"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan", order_by="VehiclePurchaseOrderInvoice.created_at"
    )

    @property
    def code(self) -> str:
        return f"OC-{self.sequence_number}"


class VehiclePurchaseOrderLine(Base):
    """What was ordered — by spec (brand/model/version/year/color), not a
    catalog SKU, since the physical units (with their own VIN) don't exist
    yet. quantity_received is never stored — it's however many
    DealershipVehicle rows point back at this line."""

    __tablename__ = "vehicle_purchase_order_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    brand: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str | None] = mapped_column(String(40), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    purchase_order: Mapped["VehiclePurchaseOrder"] = relationship(back_populates="lines")


class VehiclePurchaseOrderReception(Base):
    """A lightweight header grouping "these VINs arrived together on this
    date" — purely for traceability across an OC's multiple deliveries, no
    business logic reads it. Each unit it covers is a DealershipVehicle row
    with reception_id pointing back here."""

    __tablename__ = "vehicle_purchase_order_receptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    received_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    purchase_order: Mapped["VehiclePurchaseOrder"] = relationship(back_populates="receptions")


class VehiclePurchaseOrderInvoice(Base):
    """The supplier's invoice for (some of) the units received under an OC —
    registered by Administración, not Compras (see router.py's module
    gate). This is the moment each covered unit's real cost_price is fixed:
    total_amount is split evenly across the units it covers (service.py),
    replacing whatever estimated/blank cost they had since reception."""

    __tablename__ = "vehicle_purchase_order_invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_purchase_orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_number: Mapped[str] = mapped_column(String(60), nullable=False)
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    issued_at: Mapped[date] = mapped_column(Date, nullable=False)
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    purchase_order: Mapped["VehiclePurchaseOrder"] = relationship(back_populates="invoices")
