"""holding-wide vehicle brand/model catalog

Revision ID: 31254278989d
Revises: d29b3e6c8f14
Create Date: 2026-09-15
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "31254278989d"
down_revision: str | Sequence[str] | None = "d29b3e6c8f14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Preloaded for every holding — new ones at creation time
# (VehicleCatalogService.seed_default_brands), existing ones backfilled here.
# Models start empty; an admin adds them from Ajustes as needed.
DEFAULT_BRANDS = [
    "Toyota",
    "Chevrolet",
    "Ford",
    "JAC",
    "Chery",
    "Hyundai",
    "Kia",
    "Mitsubishi",
    "Dongfeng",
    "Great Wall",
    "Mazda",
    "Jeep",
]


def upgrade() -> None:
    op.create_table(
        "vehicle_brands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("holding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["holding_id"], ["holdings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicle_brands_holding_id", "vehicle_brands", ["holding_id"])

    op.create_table(
        "vehicle_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["brand_id"], ["vehicle_brands.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicle_models_brand_id", "vehicle_models", ["brand_id"])

    connection = op.get_bind()
    holding_ids = [row[0] for row in connection.execute(sa.text("SELECT id FROM holdings")).fetchall()]
    insert_brand = sa.text(
        "INSERT INTO vehicle_brands (id, holding_id, name, is_active) "
        "VALUES (:id, :holding_id, :name, true)"
    )
    for holding_id in holding_ids:
        for name in DEFAULT_BRANDS:
            connection.execute(insert_brand, {"id": str(uuid.uuid4()), "holding_id": str(holding_id), "name": name})


def downgrade() -> None:
    op.drop_index("ix_vehicle_models_brand_id", table_name="vehicle_models")
    op.drop_table("vehicle_models")
    op.drop_index("ix_vehicle_brands_holding_id", table_name="vehicle_brands")
    op.drop_table("vehicle_brands")
