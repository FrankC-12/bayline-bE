"""add RETRABAJO to service_order_type — own migration since Postgres
requires a new enum value to be committed before it can be used in a row

Revision ID: d4a8c2e6f157
Revises: c8e5b1f4a923
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4a8c2e6f157"
down_revision: str | Sequence[str] | None = "c8e5b1f4a923"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE service_order_type ADD VALUE IF NOT EXISTS 'RETRABAJO'")


def downgrade() -> None:
    pass
