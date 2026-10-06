"""service_order_tasks cronómetro — manual start/pause timer, independent of
task status. timer_started_at marks the currently-running segment (null
while paused); timer_accumulated_seconds sums every previously-closed segment.

Revision ID: 8f1b6c4d2a7e
Revises: 73ec7ccf0b94
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8f1b6c4d2a7e"
down_revision: str | Sequence[str] | None = "73ec7ccf0b94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_order_tasks",
        sa.Column("timer_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "service_order_tasks",
        sa.Column("timer_accumulated_seconds", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("service_order_tasks", "timer_accumulated_seconds")
    op.drop_column("service_order_tasks", "timer_started_at")
