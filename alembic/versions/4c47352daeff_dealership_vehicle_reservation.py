"""reserving a dealership vehicle now captures client, salesperson, deposit
and validity — and locks the unit to that salesperson until it's released
or sold

Revision ID: 4c47352daeff
Revises: 080f5c496223
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "4c47352daeff"
down_revision: str | Sequence[str] | None = "080f5c496223"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dealership_vehicles",
        sa.Column("reserved_client_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "dealership_vehicles",
        sa.Column("reserved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("dealership_vehicles", sa.Column("deposit_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("dealership_vehicles", sa.Column("reservation_expires_at", sa.Date(), nullable=True))
    op.add_column(
        "dealership_vehicles", sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_foreign_key(
        "fk_dealership_vehicles_reserved_client_id", "dealership_vehicles", "clients",
        ["reserved_client_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_dealership_vehicles_reserved_by_user_id", "dealership_vehicles", "users",
        ["reserved_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_dealership_vehicles_reserved_by_user_id", "dealership_vehicles", type_="foreignkey")
    op.drop_constraint("fk_dealership_vehicles_reserved_client_id", "dealership_vehicles", type_="foreignkey")

    op.drop_column("dealership_vehicles", "reserved_at")
    op.drop_column("dealership_vehicles", "reservation_expires_at")
    op.drop_column("dealership_vehicles", "deposit_amount")
    op.drop_column("dealership_vehicles", "reserved_by_user_id")
    op.drop_column("dealership_vehicles", "reserved_client_id")
