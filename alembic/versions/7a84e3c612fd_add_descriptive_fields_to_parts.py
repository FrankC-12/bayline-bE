"""add descriptive fields to parts

Revision ID: 7a84e3c612fd
Revises: ee9f0db9c15d
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7a84e3c612fd"
down_revision: str | Sequence[str] | None = "ee9f0db9c15d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "parts", sa.Column("category", sa.String(80), server_default="Sin categoría", nullable=False)
    )
    op.add_column("parts", sa.Column("brand", sa.String(80), server_default="Sin marca", nullable=False))
    op.add_column(
        "parts", sa.Column("application", sa.String(180), server_default="Universal", nullable=False)
    )
    op.add_column("parts", sa.Column("unit", sa.String(30), server_default="Unidad", nullable=False))


def downgrade() -> None:
    op.drop_column("parts", "unit")
    op.drop_column("parts", "application")
    op.drop_column("parts", "brand")
    op.drop_column("parts", "category")
