"""Reserve workshop requests at submission; consume only on warehouse dispatch."""

import sqlalchemy as sa

from alembic import op

revision = "c9f672a4d813"
down_revision = "a7d84c219f60"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "warehouses",
        sa.Column("is_workshop_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(sa.text("""UPDATE warehouses SET is_workshop_default = true WHERE id IN (
        SELECT DISTINCT ON (filial_id) id FROM warehouses WHERE is_active = true
        ORDER BY filial_id, created_at, id)"""))
    op.create_index(
        "uq_workshop_warehouse_filial",
        "warehouses",
        ["filial_id"],
        unique=True,
        postgresql_where=sa.text("is_workshop_default = true"),
    )
    op.add_column(
        "service_order_transfers",
        sa.Column("warehouse_id", sa.UUID(), sa.ForeignKey("warehouses.id", ondelete="RESTRICT")),
    )
    op.create_index(
        "ix_service_order_transfers_warehouse_id", "service_order_transfers", ["warehouse_id"]
    )
    op.add_column(
        "service_order_transfers",
        sa.Column("stock_deducted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for name in ("preparation_started_at", "picked_up_at", "backorder_notified_at"):
        op.add_column("service_order_transfers", sa.Column(name, sa.DateTime(timezone=True)))
    for name in ("preparation_started_by_user_id", "picked_up_by_user_id"):
        op.add_column(
            "service_order_transfers",
            sa.Column(name, sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        )
    op.add_column("service_order_transfers", sa.Column("pickup_photo_url", sa.String(500)))
    op.add_column(
        "service_order_transfer_lines",
        sa.Column("shortfall_quantity", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "warehouse_transfers",
        sa.Column(
            "workshop_request_id",
            sa.UUID(),
            sa.ForeignKey("service_order_transfers.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_warehouse_transfers_workshop_request_id", "warehouse_transfers", ["workshop_request_id"]
    )
    op.add_column(
        "warehouse_transfer_lines",
        sa.Column(
            "workshop_request_line_id",
            sa.UUID(),
            sa.ForeignKey("service_order_transfer_lines.id", ondelete="SET NULL"),
        ),
    )
    # Previous Pedido requests already consumed lots. Never consume them twice.
    op.execute(
        sa.text(
            "UPDATE service_order_transfers SET stock_deducted = true "
            "WHERE status IN ('PEDIDO', 'COMPLETADO')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE service_order_transfers SET picked_up_at = completed_at "
            "WHERE status = 'COMPLETADO'"
        )
    )
    op.execute(sa.text("""UPDATE service_order_transfers AS request SET warehouse_id = warehouse.id
        FROM warehouses AS warehouse, service_orders AS orders
        WHERE request.service_order_id = orders.id AND warehouse.filial_id = orders.filial_id
          AND warehouse.is_workshop_default = true"""))


def downgrade():
    op.drop_column("warehouse_transfer_lines", "workshop_request_line_id")
    op.drop_index("ix_warehouse_transfers_workshop_request_id", table_name="warehouse_transfers")
    op.drop_column("warehouse_transfers", "workshop_request_id")
    op.drop_column("service_order_transfer_lines", "shortfall_quantity")
    for name in (
        "pickup_photo_url",
        "picked_up_by_user_id",
        "preparation_started_by_user_id",
        "backorder_notified_at",
        "picked_up_at",
        "preparation_started_at",
        "stock_deducted",
        "warehouse_id",
    ):
        op.drop_column("service_order_transfers", name)
    op.drop_index("uq_workshop_warehouse_filial", table_name="warehouses")
    op.drop_column("warehouses", "is_workshop_default")
