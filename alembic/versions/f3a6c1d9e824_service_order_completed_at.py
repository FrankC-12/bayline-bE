"""service order completed_at

Revision ID: f3a6c1d9e824
Revises: b4df079fe642
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a6c1d9e824"
down_revision: str | Sequence[str] | None = "b4df079fe642"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("service_orders", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    # Backfill existing already-completed/closed orders so the elapsed-time
    # counter doesn't show a huge running time for them until their next
    # (impossible, since completado only moves forward) transition — use
    # updated_at as the best available approximation of when each entered
    # completado; orden_cerrada rows use closed_at instead, since that's
    # exactly when they left completado.
    op.execute(
        """
        UPDATE service_orders
        SET completed_at = COALESCE(closed_at, updated_at)
        WHERE status IN ('COMPLETADO', 'ORDEN_CERRADA') AND completed_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("service_orders", "completed_at")
