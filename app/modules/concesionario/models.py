import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.modules.concesionario.enums import (
    FuelType,
    SaleType,
    TransmissionType,
    VehicleCondition,
    VehicleStatus,
)


class DealershipVehicle(Base):
    """A vehicle in the dealership's own catalog — unrelated to the vehicles
    owned by workshop clients (Clientes y Vehículos)."""

    __tablename__ = "dealership_vehicles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus, name="dealership_vehicle_status"),
        nullable=False,
        default=VehicleStatus.EN_TRANSITO,
    )
    condition: Mapped[VehicleCondition] = mapped_column(
        Enum(VehicleCondition, name="dealership_vehicle_condition"), nullable=False
    )
    brand: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fuel_type: Mapped[FuelType | None] = mapped_column(
        Enum(FuelType, name="dealership_vehicle_fuel_type"), nullable=True
    )
    transmission: Mapped[TransmissionType | None] = mapped_column(
        Enum(TransmissionType, name="dealership_vehicle_transmission"), nullable=True
    )
    vin: Mapped[str] = mapped_column(String(17), nullable=False)
    plate: Mapped[str | None] = mapped_column(String(10), nullable=True)
    sku: Mapped[str] = mapped_column(String(40), nullable=False)
    price_cash: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    price_financed: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    cost_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    iva_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=16)
    igtf_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=3)
    luxury_tax_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    financing_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    financing_external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    images: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def iva_amount(self) -> float:
        return round(float(self.price_cash) * float(self.iva_percentage) / 100, 2)

    @property
    def igtf_amount(self) -> float:
        if self.price_currency != "USD":
            return 0.0
        return round(float(self.price_cash) * float(self.igtf_percentage) / 100, 2)

    @property
    def luxury_tax_amount(self) -> float:
        return round(float(self.price_cash) * float(self.luxury_tax_percentage) / 100, 2)

    @property
    def cash_total(self) -> float:
        return round(float(self.price_cash) + self.iva_amount + self.igtf_amount + self.luxury_tax_amount, 2)


class VehicleSale(Base):
    """Auto-created when a DealershipVehicle's status is set to 'vendido'."""

    __tablename__ = "vehicle_sales"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dealership_vehicles.id", ondelete="RESTRICT"), nullable=False
    )
    client_name: Mapped[str] = mapped_column(String(150), nullable=False)
    client_document: Mapped[str | None] = mapped_column(String(20), nullable=True)
    advisor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    sale_type: Mapped[SaleType] = mapped_column(Enum(SaleType, name="vehicle_sale_type"), nullable=False)
    final_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def code(self) -> str:
        return f"CV-{self.sequence_number:04d}"
