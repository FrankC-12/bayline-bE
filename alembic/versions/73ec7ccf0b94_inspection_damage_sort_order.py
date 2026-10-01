"""inspection_damages.sort_order — explicit submission order, since all
damages in one inspection share the same created_at (same transaction) and
so can't be used to reliably preserve the order the client sent them in.

Revision ID: 73ec7ccf0b94
Revises: 326cdc680903
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "73ec7ccf0b94"
down_revision: str | Sequence[str] | None = "326cdc680903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inspection_damages",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("inspection_damages", "sort_order")
