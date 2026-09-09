"""Separate invoice/payment issuance from order closing.

Revision ID: a4d37e5fb243
Revises: f3c26d4ea132
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "a4d37e5fb243"
down_revision = "f3c26d4ea132"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "service_orders", sa.Column("invoiced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "service_order_invoices",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "service_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("service_orders.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "issued_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("total_usd", sa.Numeric(18, 2), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("service_order_invoices")
    op.drop_column("service_orders", "invoiced_at")
