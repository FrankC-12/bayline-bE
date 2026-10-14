"""Indexes for chronological movement pages per filial and warehouse."""

from alembic import op

revision = "f3b7821a9d65"
down_revision = "e2a5c81b7d40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_stock_movements_filial_chronology", "stock_movements", ["filial_id", "created_at", "id"]
    )
    op.create_index(
        "ix_stock_movements_warehouse_chronology",
        "stock_movements",
        ["filial_id", "warehouse_id", "created_at", "id"],
    )


def downgrade():
    op.drop_index("ix_stock_movements_warehouse_chronology", table_name="stock_movements")
    op.drop_index("ix_stock_movements_filial_chronology", table_name="stock_movements")
