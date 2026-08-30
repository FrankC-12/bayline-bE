import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.post_ventas.enums import CATEGORY_PREFIXES, TemparioCategory


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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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