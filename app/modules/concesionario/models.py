import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, func
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
    version: Mapped[str | None] = mapped_column(String(60), nullable=True)
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
    # Whether cost_price is an actual acquisition cost or a stand-in guess —
    # true from reception (see ComprasService.add_reception) until the
    # supplier invoice linked to the OC is registered and fixes the real
    # cost, or a stand-in guess for a unit added by hand with no OC behind it.
    cost_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    # Traceability back to Compras — all three null for a unit added by hand
    # (AddVehicleModal), all three set for one born from a reception.
    purchase_order_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_purchase_order_lines.id", ondelete="SET NULL"), nullable=True
    )
    reception_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_purchase_order_receptions.id", ondelete="SET NULL"), nullable=True
    )
    purchase_order_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_purchase_order_invoices.id", ondelete="SET NULL"), nullable=True
    )
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    iva_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=16)
    igtf_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=3)
    luxury_tax_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    financing_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    financing_external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    images: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Set only while status == RESERVADO — reservar requires all four
    # together (see ConcesionarioService.reserve_vehicle) and they're
    # cleared as soon as the unit leaves that status (released or sold).
    # reserved_by_user_id is the "vendedor" the reservation is locked to:
    # any other non-admin caller is blocked from touching this vehicle
    # while it's reserved.
    reserved_client_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True
    )
    reserved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    deposit_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    reservation_expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def iva_amount(self) -> float:
        return round(float(self.price_cash) * float(self.iva_percentage) / 100, 2)

    @property
    def igtf_amount(self) -> float:
        """An ESTIMATE only — "if this were paid entirely in foreign
        currency, IGTF would be this much." IGTF is not part of the
        vehicle's list price (see cash_total): it depends on how the sale
        is actually paid, which isn't known until checkout. The real amount
        charged is computed at sale time in ConcesionarioService.update_vehicle
        via calculate_payment (service_orders/billing.py), on whatever
        portion actually lands in USD."""
        if self.price_currency != "USD":
            return 0.0
        taxable_amount = float(self.price_cash) + self.iva_amount
        return round(taxable_amount * float(self.igtf_percentage) / 100, 2)

    @property
    def luxury_tax_amount(self) -> float:
        return round(float(self.price_cash) * float(self.luxury_tax_percentage) / 100, 2)

    @property
    def cash_total(self) -> float:
        """The vehicle's list price — base + IVA + luxury tax. Deliberately
        excludes IGTF, which only applies to whatever portion of an actual
        sale is paid in foreign currency — see igtf_amount's docstring."""
        return round(float(self.price_cash) + self.iva_amount + self.luxury_tax_amount, 2)


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
    # Only set for sale_type=contado — how the payment was actually split,
    # since that's what determines the IGTF charged (see igtf_amount below).
    # Null for sale_type=financiado, which never touches IGTF.
    payment_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    usd_base: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    igtf_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    bcv_rate: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    # base + IVA + luxury tax + igtf_amount (contado) or price_financed
    # (financiado) — the actual amount charged, computed once at sale time.
    final_price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Set only when final_price came out below the vehicle's cost_price —
    # requires an "administracion"-level override, recorded here alongside
    # who authorized it. False/null for every ordinary, at-or-above-cost sale.
    below_cost_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    below_cost_override_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    authorized_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def code(self) -> str:
        return f"CV-{self.sequence_number:04d}"
