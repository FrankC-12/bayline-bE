"""upsell postponed tracking (for conversion-rate KPI)

Revision ID: d5e8b3a1c9f2
Revises: c1f7a2d9e3b5
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5e8b3a1c9f2"
down_revision: str | Sequence[str] | None = "c1f7a2d9e3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "upsells",
        sa.Column("was_postponed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("upsells", sa.Column("postponed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("upsells", "postponed_at")
    op.drop_column("upsells", "was_postponed")
