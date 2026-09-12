"""workshop warranty duration settings — days/km the shop's own labor+parts
warranty lasts, per filial, used to compute each new warranty's expiration

Revision ID: a3c8e15f7d24
Revises: f1a4d7c2e963
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3c8e15f7d24"
down_revision: str | Sequence[str] | None = "f1a4d7c2e963"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "labor_settings",
        sa.Column("workshop_warranty_days", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column(
        "labor_settings",
        sa.Column("workshop_warranty_km", sa.Integer(), nullable=False, server_default="5000"),
    )


def downgrade() -> None:
    op.drop_column("labor_settings", "workshop_warranty_km")
    op.drop_column("labor_settings", "workshop_warranty_days")
