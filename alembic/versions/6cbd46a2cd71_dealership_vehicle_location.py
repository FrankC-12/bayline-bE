"""S3 · Agregar ubicación física al vehículo de inventario —
DealershipVehicle.location (patio / showroom / sucursal), nullable since
existing rows predate the field.

Revision ID: 6cbd46a2cd71
Revises: 501e255c8dd4
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "6cbd46a2cd71"
down_revision: str | Sequence[str] | None = "501e255c8dd4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dealership_vehicle_location = postgresql.ENUM(
        "PATIO", "SHOWROOM", "SUCURSAL", name="dealership_vehicle_location"
    )
    dealership_vehicle_location.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "dealership_vehicles",
        sa.Column("location", dealership_vehicle_location, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dealership_vehicles", "location")
    op.execute("DROP TYPE IF EXISTS dealership_vehicle_location")
