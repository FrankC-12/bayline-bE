"""Persist service-order parts margin and cost snapshots.

Revision ID: e2b15c3d9021
Revises: d1f04a2b8910
"""

import sqlalchemy as sa

from alembic import op

revision = "e2b15c3d9021"
down_revision = "d1f04a2b8910"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "service_orders",
        sa.Column(
            "discount_label",
            sa.String(60),
            nullable=False,
            server_default="Costo + 30% (Sin Descuento)",
        ),
    )
    op.alter_column("service_order_transfer_lines", "unit_price", type_=sa.Numeric(18, 6))
    op.add_column("service_order_transfer_lines", sa.Column("cost_total", sa.Numeric(18, 2)))
    op.add_column("service_order_transfer_lines", sa.Column("line_total", sa.Numeric(18, 2)))
    # Legacy ODT prices were always round(cost * 1.30, 2). Recover the
    # cent-denominated snapshot from that price, never from today's inventory.
    op.execute(
        "UPDATE service_order_transfer_lines SET "
        "cost_total = round(unit_price / 1.30, 2) * quantity, "
        "line_total = round(unit_price * quantity, 2)"
    )
    op.alter_column("service_order_transfer_lines", "cost_total", nullable=False)
    op.alter_column("service_order_transfer_lines", "line_total", nullable=False)


def downgrade():
    op.drop_column("service_order_transfer_lines", "line_total")
    op.drop_column("service_order_transfer_lines", "cost_total")
    op.alter_column("service_order_transfer_lines", "unit_price", type_=sa.Numeric(10, 2))
    op.drop_column("service_orders", "discount_label")
