"""rework claims (garantía de taller) — Torre de Control rework rate

Revision ID: 9c2e7f4b8a15
Revises: 8b1f5d3a7c62
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9c2e7f4b8a15"
down_revision: str | Sequence[str] | None = "8b1f5d3a7c62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rework_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tempario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("part_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_cause", sa.String(300), nullable=False),
        sa.Column("claimed_at", sa.Date(), nullable=False),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_order_id"], ["service_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tempario_id"], ["temparios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rework_claims_filial_id", "rework_claims", ["filial_id"])
    op.create_index("ix_rework_claims_service_order_id", "rework_claims", ["service_order_id"])


def downgrade() -> None:
    op.drop_index("ix_rework_claims_service_order_id", table_name="rework_claims")
    op.drop_index("ix_rework_claims_filial_id", table_name="rework_claims")
    op.drop_table("rework_claims")
