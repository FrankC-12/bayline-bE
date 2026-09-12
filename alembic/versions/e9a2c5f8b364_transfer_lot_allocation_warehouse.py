"""add warehouse_id to service_order_transfer_lot_allocations — an ODT is
filial-wide, not scoped to one warehouse like a counter sale, so a line's
allocation needs its own warehouse per lot consumed

Revision ID: e9a2c5f8b364
Revises: d8f3b6c1a247
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e9a2c5f8b364"
down_revision: str | Sequence[str] | None = "d8f3b6c1a247"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_order_transfer_lot_allocations",
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE service_order_transfer_lot_allocations AS a
        SET warehouse_id = part_lots.warehouse_id
        FROM part_lots
        WHERE part_lots.id = a.lot_id
        """
    )
    op.alter_column("service_order_transfer_lot_allocations", "warehouse_id", nullable=False)
    op.create_foreign_key(
        "fk_service_order_transfer_lot_allocations_warehouse_id",
        "service_order_transfer_lot_allocations",
        "warehouses",
        ["warehouse_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_service_order_transfer_lot_allocations_warehouse_id",
        "service_order_transfer_lot_allocations",
        type_="foreignkey",
    )
    op.drop_column("service_order_transfer_lot_allocations", "warehouse_id")
