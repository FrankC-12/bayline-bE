import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"
    __table_args__ = (UniqueConstraint("currency", "value_date", name="uq_exchange_rate_currency_date"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    rate_ves: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="https://www.bcv.org.ve/")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
