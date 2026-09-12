"""accounts.opening_balance — lets a new account start with the balance it
already carried before Bayline began tracking its movements

Revision ID: f3b6d1e8a742
Revises: a1f4c7e29b56
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3b6d1e8a742"
down_revision: str | Sequence[str] | None = "a1f4c7e29b56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("opening_balance", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("accounts", "opening_balance")
