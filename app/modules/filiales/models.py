import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Filial(Base):
    """A dealership/workshop affiliated with an importer network, not necessarily owned by it."""

    __tablename__ = "filiales"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    holding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    slug: Mapped[str] = mapped_column(String(150), nullable=False, index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    rif: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fiscal_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    represented_brands: Mapped[str | None] = mapped_column(String(300), nullable=True)
    special_taxpayer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    invoice_series: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
