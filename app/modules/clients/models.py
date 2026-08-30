import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, func
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
    plate: Mapped[str] = mapped_column(String(8), nullable=False)
    color: Mapped[str | None] = mapped_column(String(40), nullable=True)
    upholstery: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fuel_type: Mapped[FuelType | None] = mapped_column(Enum(FuelType, name="fuel_type"), nullable=True)
    transmission: Mapped[TransmissionType | None] = mapped_column(
        Enum(TransmissionType, name="transmission_type"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    client: Mapped["Client"] = relationship(back_populates="vehicles")