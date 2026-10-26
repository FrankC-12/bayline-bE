"""Separate parts list price from FIFO acquisition cost.

Revision ID: d1a804be6202
Revises: c0e671af4201
"""

from alembic import op
import sqlalchemy as sa

revision = "d1a804be6202"
down_revision = "c0e671af4201"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "part_sales", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "upsell_parts", sa.Column("unit_price_snapshot", sa.Numeric(18, 6), nullable=True)
    )
    op.add_column(
        "holding_audit_events", sa.Column("resource_label", sa.String(200), nullable=True)
    )
    op.add_column("vehicle_sales", sa.Column("cost_snapshot", sa.Numeric(18, 2), nullable=True))
    op.add_column(
        "upsells", sa.Column("approved_amount_snapshot", sa.Numeric(18, 2), nullable=True)
    )
    for name, length in [
        ("legal_name", 200),
        ("rif", 20),
        ("fiscal_address", 500),
        ("business_type", 30),
        ("represented_brands", 300),
        ("invoice_series", 20),
    ]:
        op.add_column("filiales", sa.Column(name, sa.String(length), nullable=True))
    op.add_column(
        "filiales",
        sa.Column("special_taxpayer", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("parts", sa.Column("list_price", sa.Numeric(18, 2), nullable=True))
    # Freeze the currently displayed reference price once. Never rewrite invoices
    # or approved ODT lines. An item without cost history remains unconfigured.
    op.execute("""
        UPDATE parts SET list_price = ROUND((SELECT unit_cost * 1.30
            FROM part_lots WHERE part_lots.part_id = parts.id
            ORDER BY received_at DESC, id DESC LIMIT 1), 2)
    """)
    op.create_check_constraint(
        "ck_parts_list_price_nonnegative", "parts", "list_price IS NULL OR list_price >= 0"
    )


def downgrade():
    op.drop_column("part_sales", "completed_at")
    op.drop_column("upsell_parts", "unit_price_snapshot")
    op.drop_column("holding_audit_events", "resource_label")
    op.drop_column("vehicle_sales", "cost_snapshot")
    op.drop_column("upsells", "approved_amount_snapshot")
    for name in [
        "special_taxpayer",
        "invoice_series",
        "represented_brands",
        "business_type",
        "fiscal_address",
        "rif",
        "legal_name",
    ]:
        op.drop_column("filiales", name)
    op.drop_constraint("ck_parts_list_price_nonnegative", "parts", type_="check")
    op.drop_column("parts", "list_price")
