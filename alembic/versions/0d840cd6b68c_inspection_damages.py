"""Mapa de daños en Inspecciones Preliminares — inspection_damages, one row
per tapped point on the vehicle diagram (x/y normalized 0-1, zone/kind/
severity free text, an open growable catalog rather than a Postgres enum).

Revision ID: 0d840cd6b68c
Revises: 6cbd46a2cd71
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0d840cd6b68c"
down_revision: str | Sequence[str] | None = "6cbd46a2cd71"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inspection_damages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("inspection_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("x", sa.Numeric(6, 4), nullable=False),
        sa.Column("y", sa.Numeric(6, 4), nullable=False),
        sa.Column("zone", sa.String(60), nullable=False),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(30), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["inspection_id"], ["preliminary_inspections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inspection_damages_inspection_id", "inspection_damages", ["inspection_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_inspection_damages_inspection_id", table_name="inspection_damages")
    op.drop_table("inspection_damages")
