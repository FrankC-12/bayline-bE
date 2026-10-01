import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.modules.inspections.enums import InspectionStatus


class PreliminaryInspection(Base):
    """The intake inspection performed when a vehicle enters the workshop.
    Optionally links to a ServiceOrder once one is created for it."""

    __tablename__ = "preliminary_inspections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filial_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    inspector_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    service_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[InspectionStatus] = mapped_column(
        Enum(InspectionStatus, name="inspection_status"),
        nullable=False,
        default=InspectionStatus.COMPLETADA,
    )
    # General photos of the vehicle at intake, not tied to a specific marked
    # damage (odometer, fuel gauge, overall condition...) — same pattern as
    # PartReturn.photo_urls.
    photo_urls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    damages: Mapped[list["InspectionDamage"]] = relationship(
        back_populates="inspection", cascade="all, delete-orphan", order_by="InspectionDamage.sort_order"
    )


class InspectionDamage(Base):
    """One tapped point on the vehicle diagram during an inspection — where
    a technician marked a scratch/dent/etc. zone/kind/severity are free text
    (an open, growable catalog, same idea as AddVehicleModal's year/color
    "+" fields) rather than a Postgres enum, so a missing option never needs
    a migration."""

    __tablename__ = "inspection_damages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("preliminary_inspections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Explicit submission order — all damages in one inspection are created
    # in the same transaction, so created_at alone ties and can't be trusted
    # to preserve the order the client sent them in (which the client needs,
    # to correlate each damage's id back to its own pending photo upload).
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Normalized (0-1) position on the diagram image/SVG, not pixels — so
    # the point still lands correctly regardless of how large the diagram
    # renders on a given screen.
    x: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    y: Mapped[float] = mapped_column(Numeric(6, 4), nullable=False)
    zone: Mapped[str] = mapped_column(String(60), nullable=False)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    inspection: Mapped["PreliminaryInspection"] = relationship(back_populates="damages")