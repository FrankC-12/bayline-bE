"""Persist FIFO sale costs and lot allocations.

Revision ID: d1f04a2b8910
Revises: c3f1a9d4e782
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d1f04a2b8910"
down_revision = "c3f1a9d4e782"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("part_sale_lines", "unit_price", type_=sa.Numeric(18, 6))
    op.alter_column("part_sale_lines", "unit_cost", type_=sa.Numeric(18, 6))
    # Historical sales have no recoverable warehouse/lot provenance.
    op.add_column(
        "part_sale_lines",
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("part_sale_lines", sa.Column("line_total", sa.Numeric(18, 2), nullable=True))
    op.execute("UPDATE part_sale_lines SET line_total = round(unit_price * quantity, 2)")
    op.alter_column("part_sale_lines", "line_total", nullable=False)
    op.create_table(
        "part_sale_lot_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "part_sale_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("part_sale_lines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "lot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("part_lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(10, 2), nullable=False),
    )
    op.create_index(
        "ix_part_sale_lot_allocations_part_sale_line_id",
        "part_sale_lot_allocations",
        ["part_sale_line_id"],
    )


def downgrade():
    op.drop_table("part_sale_lot_allocations")
    op.drop_column("part_sale_lines", "line_total")
    op.drop_column("part_sale_lines", "warehouse_id")
    op.alter_column("part_sale_lines", "unit_cost", type_=sa.Numeric(10, 2))
    op.alter_column("part_sale_lines", "unit_price", type_=sa.Numeric(10, 2))
