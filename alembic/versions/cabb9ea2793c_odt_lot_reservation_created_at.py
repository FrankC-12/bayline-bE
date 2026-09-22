"""an ODT line's FIFO lot preview now doubles as a soft, 5-minute
reservation — another line can't price itself against the same units while
this one's preview is still fresh, so the price shown in Pendiente doesn't
drift from what actually gets dispatched

Revision ID: cabb9ea2793c
Revises: c2da052f1bc8
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "cabb9ea2793c"
down_revision: str | Sequence[str] | None = "c2da052f1bc8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_order_transfer_lot_allocations",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_column("service_order_transfer_lot_allocations", "created_at")
