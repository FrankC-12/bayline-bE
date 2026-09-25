"""promised_at becomes a datetime

Revision ID: d8b1f4a2c6e7
Revises: c7e2b5a1f930
Create Date: 2026-09-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d8b1f4a2c6e7"
down_revision: str | Sequence[str] | None = "c7e2b5a1f930"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # "Fecha prometida de entrega" (date only) becomes "Fecha de inicio de
    # ODS" (date + 30-minute time slot). Existing rows only ever had a date,
    # so they land at midnight — no time-of-day information existed to
    # recover, and no code reads promised_at's time component for these.
    op.alter_column(
        "service_orders",
        "promised_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="promised_at::timestamptz",
    )


def downgrade() -> None:
    op.alter_column(
        "service_orders",
        "promised_at",
        type_=sa.Date(),
        postgresql_using="promised_at::date",
    )
