"""Holding-level registers; financial evidence is immutable and tenant scoped."""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HoldingRegister(Base):
    __tablename__ = "holding_registers"
    __table_args__ = (
        UniqueConstraint("holding_id", "request_id", name="uq_holding_register_request"),
        Index("ix_holding_register_scope", "holding_id", "kind", "entry_date"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    holding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("holdings.id", ondelete="CASCADE")
    )
    filial_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="RESTRICT"), nullable=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(30))
    entry_date: Mapped[date] = mapped_column(Date)
    payload: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HoldingAuditEvent(Base):
    __tablename__ = "holding_audit_events"
    __table_args__ = (Index("ix_holding_audit_chronology", "holding_id", "created_at"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    holding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("holdings.id", ondelete="CASCADE")
    )
    filial_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(30))
    entity: Mapped[str] = mapped_column(String(100))
    resource_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    changed_fields: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
