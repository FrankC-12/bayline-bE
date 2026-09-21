"""separate IGTF from the vehicle's list price — compute it at sale time
on whatever portion is actually paid in foreign currency

Revision ID: 7a1c4e9d2b83
Revises: 2f7b4e1a9c56
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7a1c4e9d2b83"
down_revision: str | Sequence[str] | None = "2f7b4e1a9c56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vehicle_sales", sa.Column("payment_method", sa.String(10), nullable=True))
    op.add_column("vehicle_sales", sa.Column("usd_base", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "vehicle_sales", sa.Column("igtf_amount", sa.Numeric(12, 2), nullable=False, server_default="0")
    )
    op.add_column("vehicle_sales", sa.Column("bcv_rate", sa.Numeric(18, 8), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicle_sales", "bcv_rate")
    op.drop_column("vehicle_sales", "igtf_amount")
    op.drop_column("vehicle_sales", "usd_base")
    op.drop_column("vehicle_sales", "payment_method")
