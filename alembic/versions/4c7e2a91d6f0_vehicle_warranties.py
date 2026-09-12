"""factory warranty per vehicle (VIN-keyed)

Revision ID: 4c7e2a91d6f0
Revises: 7f4a1c9e6b23
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4c7e2a91d6f0"
down_revision: str | Sequence[str] | None = "7f4a1c9e6b23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "labor_settings",
        sa.Column("vehicle_warranty_default_months", sa.Integer(), nullable=False, server_default="36"),
    )

    warranty_source = postgresql.ENUM("VENTA", "MANUAL", name="vehicle_warranty_source", create_type=False)
    warranty_source.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "vehicle_warranties",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vin", sa.String(17), nullable=False),
        sa.Column("brand", sa.String(60), nullable=False),
        sa.Column("model", sa.String(60), nullable=True),
        sa.Column("starts_at", sa.Date(), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=True),
        sa.Column("duration_km", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("source", warranty_source, nullable=False),
        sa.Column("dealership_vehicle_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dealership_vehicle_id"], ["dealership_vehicles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filial_id", "vin", name="uq_vehicle_warranty_filial_vin"),
    )
    op.create_index("ix_vehicle_warranties_filial_id", "vehicle_warranties", ["filial_id"])


def downgrade() -> None:
    op.drop_index("ix_vehicle_warranties_filial_id", table_name="vehicle_warranties")
    op.drop_table("vehicle_warranties")
    postgresql.ENUM(name="vehicle_warranty_source").drop(op.get_bind(), checkfirst=True)
    op.drop_column("labor_settings", "vehicle_warranty_default_months")
