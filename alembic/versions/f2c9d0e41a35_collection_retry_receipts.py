"""Persist collection retry receipts atomically with automatic income."""

import sqlalchemy as sa

from alembic import op

revision = "f2c9d0e41a35"
down_revision = "e1b8c9d30f24"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_order_collection_requests",
        sa.Column("request_id", sa.UUID(), primary_key=True),
        sa.Column(
            "invoice_id",
            sa.UUID(),
            sa.ForeignKey("service_order_invoices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "income_id",
            sa.UUID(),
            sa.ForeignKey("income_entries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_service_order_collection_requests_invoice_id",
        "service_order_collection_requests",
        ["invoice_id"],
    )


def downgrade():
    op.drop_table("service_order_collection_requests")
