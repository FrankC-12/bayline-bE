import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.parts.enums import PartAvailability, PartSaleStatus, ReturnCondition, ReturnReason


class Part(Base):
    """A catalog item sold directly to walk-in customers."""

    __tablename__ = "parts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    min_stock: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    availability: Mapped[PartAvailability] = mapped_column(
        Enum(PartAvailability, name="part_availability"),
        nullable=False,
        default=PartAvailability.DISPONIBLE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PartSale(Base):
    """A direct sale of parts to a walk-in customer (not tied to a Client record)."""

    __tablename__ = "part_sales"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    client_name: Mapped[str] = mapped_column(String(150), nullable=False)
    client_document: Mapped[str | None] = mapped_column(String(20), nullable=True)
    request_reason: Mapped[str] = mapped_column(String(150), nullable=False, default="Venta de Repuestos")
    discount_label: Mapped[str] = mapped_column(
        String(60), nullable=False, default="Costo + 30% (Sin Descuento)"
    )
    status: Mapped[PartSaleStatus] = mapped_column(
        Enum(PartSaleStatus, name="part_sale_status"), nullable=False, default=PartSaleStatus.PENDIENTE
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    lines: Mapped[list["PartSaleLine"]] = relationship(
        back_populates="sale", cascade="all, delete-orphan"
    )

    @property
    def code(self) -> str:
        return f"VR-{self.sequence_number}"

    @property
    def total(self) -> float:
        return sum(float(line.unit_price) * line.quantity for line in self.lines)


class PartSaleLine(Base):
    __tablename__ = "part_sale_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    part_sale_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("part_sales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)

    sale: Mapped["PartSale"] = relationship(back_populates="lines")


class PartReturn(Base):
    """A log entry for a part returned to (or moved out of) the warehouse."""

    __tablename__ = "part_returns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    part_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parts.id", ondelete="RESTRICT"), nullable=False
    )
    condition: Mapped[ReturnCondition] = mapped_column(
        Enum(ReturnCondition, name="return_condition"), nullable=False, default=ReturnCondition.NUEVO
    )
    origin_warehouse: Mapped[str] = mapped_column(String(60), nullable=False)
    destination_warehouse: Mapped[str] = mapped_column(String(60), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[ReturnReason] = mapped_column(Enum(ReturnReason, name="return_reason"), nullable=False)
    reason_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    responsible_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())