"""Inventory mileage and its immutable sale snapshot; unknown legacy values stay NULL."""

import sqlalchemy as sa

from alembic import op

revision = "d9a6b7f20e13"
down_revision = "c8e5a4d19f02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dealership_vehicles", sa.Column("mileage", sa.Integer(), nullable=True))
    op.add_column("vehicle_sales", sa.Column("mileage_at_sale", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicle_sales", "mileage_at_sale")
    op.drop_column("dealership_vehicles", "mileage")
