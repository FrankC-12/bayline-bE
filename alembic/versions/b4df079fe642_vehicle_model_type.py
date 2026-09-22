"""vehicle model type

Revision ID: b4df079fe642
Revises: 697c3729b72b
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4df079fe642"
down_revision: str | Sequence[str] | None = "697c3729b72b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vehicle_models", sa.Column("vehicle_type", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("vehicle_models", "vehicle_type")
