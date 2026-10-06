"""Charge parts IGTF on collection rather than assuming a full USD payment.

Existing collected sales retain their frozen taxes and historic income.
Uncollected sales retain IVA, but no longer owe an assumed USD-only IGTF.
"""

from alembic import op

revision = "b6d2e8f4c901"
down_revision = "a7c3e9f12b4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE part_sales
        SET igtf_amount = 0
        WHERE NOT EXISTS (
            SELECT 1 FROM income_entries
            WHERE income_entries.source_type = 'PART_SALE'
              AND income_entries.source_id = part_sales.id
        )
    """)


def downgrade() -> None:
    # Historic collections must never be recomputed when rolling back.
    op.execute("""
        UPDATE part_sales
        SET igtf_amount = ROUND((
            COALESCE((SELECT SUM(line_total) FROM part_sale_lines
                      WHERE part_sale_lines.part_sale_id = part_sales.id), 0)
            + iva_amount
        ) * igtf_percentage / 100.0, 2)
        WHERE NOT EXISTS (
            SELECT 1 FROM income_entries
            WHERE income_entries.source_type = 'PART_SALE'
              AND income_entries.source_id = part_sales.id
        )
    """)
