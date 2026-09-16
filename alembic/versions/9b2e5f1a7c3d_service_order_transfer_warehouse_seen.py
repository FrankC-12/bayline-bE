"""add warehouse_seen flag to service_order_transfers

Revision ID: 9b2e5f1a7c3d
Revises: 14cb9c9ce2a6
Create Date: 2026-09-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9b2e5f1a7c3d"
down_revision: str | Sequence[str] | None = "14cb9c9ce2a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_order_transfers",
        sa.Column("warehouse_seen", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("service_order_transfers", "warehouse_seen")
