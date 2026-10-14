"""Unified settings, service warranties, supplier invoices and FIFO transfer lineage."""

import sqlalchemy as sa

from alembic import op

revision = "b5d10c73a982"
down_revision = "a4c93e682f17"
branch_labels = None
depends_on = None

COLUMNS = {
    "vehicle_warranties": [sa.Column("certificate_photo_url", sa.String(500))],
    "labor_settings": [
        sa.Column("bcv_rate_mode", sa.String(10), nullable=False, server_default="automatic"),
        sa.Column("field_changes", sa.JSON(), nullable=False, server_default="{}"),
    ],
    "temparios": [
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("warranty_mode", sa.String(12), nullable=False, server_default="standard"),
        sa.Column("warranty_days", sa.Integer()),
        sa.Column("warranty_km", sa.Integer()),
        sa.Column("updated_by_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
    ],
    "service_orders": [
        sa.Column("settings_snapshot", sa.JSON()),
        sa.Column("exit_mileage", sa.Integer()),
    ],
    "purchase_requests": [
        sa.Column("invoice_number", sa.String(80)),
        sa.Column("invoice_control_number", sa.String(80)),
        sa.Column("invoice_date", sa.Date()),
        sa.Column("credit_days", sa.Integer()),
        sa.Column("due_date", sa.Date()),
        sa.Column("invoice_iva_percentage", sa.Numeric(5, 2)),
        sa.Column("invoice_total", sa.Numeric(14, 2)),
        sa.Column("paid_amount_usd", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("activity", sa.JSON(), nullable=False, server_default="[]"),
    ],
    "purchase_request_lines": [
        sa.Column("received_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invoiced_unit_cost", sa.Numeric(10, 2)),
        sa.Column("receipts", sa.JSON(), nullable=False, server_default="[]"),
    ],
    "part_lots": [
        sa.Column("origin_lot_id", sa.UUID(), sa.ForeignKey("part_lots.id", ondelete="RESTRICT")),
        sa.Column(
            "origin_transfer_id",
            sa.UUID(),
            sa.ForeignKey("warehouse_transfers.id", ondelete="RESTRICT"),
        ),
    ],
}


def upgrade():
    op.execute("ALTER TYPE movement_source_type ADD VALUE IF NOT EXISTS 'PURCHASE_REQUEST'")
    for table, columns in COLUMNS.items():
        for column in columns:
            op.add_column(table, column)
    op.create_table(
        "warehouse_transfer_lot_allocations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "transfer_id",
            sa.UUID(),
            sa.ForeignKey("warehouse_transfers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "origin_lot_id",
            sa.UUID(),
            sa.ForeignKey("part_lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "destination_lot_id",
            sa.UUID(),
            sa.ForeignKey("part_lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(10, 2), nullable=False),
    )
    for name in ["transfer_id", "origin_lot_id"]:
        op.create_index(
            "ix_warehouse_transfer_lot_allocations_" + name,
            "warehouse_transfer_lot_allocations",
            [name],
        )
    op.create_table(
        "stock_out_reasons",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "holding_id",
            sa.UUID(),
            sa.ForeignKey("holdings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("movement_type", sa.String(20), nullable=False, server_default="salida"),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_stock_out_reasons_holding_id", "stock_out_reasons", ["holding_id"])
    op.create_index(
        "uq_stock_out_reasons_holding_name",
        "stock_out_reasons",
        ["holding_id", sa.text("lower(name)")],
        unique=True,
    )
    for name in [
        "Ajuste de inventario",
        "Consumo interno",
        "Daño o pérdida",
        "Devolución a proveedor",
        "Otro",
    ]:
        op.execute(
            sa.text(
                "INSERT INTO stock_out_reasons (id,holding_id,name) SELECT gen_random_uuid(),id,:name FROM holdings"
            ).bindparams(name=name)
        )
    op.execute(
        "UPDATE stock_out_reasons SET movement_type='devolucion' WHERE name='Devolución a proveedor'"
    )
    # Existing orders keep the last known settings; no historical price can be invented.
    op.execute("""UPDATE service_orders o SET settings_snapshot = json_build_object(
        'hourly_rate',s.hourly_rate,'commission_percentage',s.commission_percentage,
        'iva_percentage',s.iva_percentage,'igtf_percentage',s.igtf_percentage,
        'iva_retention_default_percentage',s.iva_retention_default_percentage,
        'islr_retention_default_percentage',s.islr_retention_default_percentage,
        'workshop_warranty_days',s.workshop_warranty_days,'workshop_warranty_km',s.workshop_warranty_km,
        'workshop_parts_warranty_days',s.workshop_parts_warranty_days,'workshop_parts_warranty_km',s.workshop_parts_warranty_km)
        FROM labor_settings s WHERE o.filial_id=s.filial_id""")
    op.execute(
        'UPDATE service_orders SET settings_snapshot=\'{"hourly_rate": 25, "commission_percentage": 30, "iva_percentage": 16, "igtf_percentage": 3, "iva_retention_default_percentage": 0, "islr_retention_default_percentage": 0, "workshop_warranty_days": 90, "workshop_warranty_km": 5000, "workshop_parts_warranty_days": 90, "workshop_parts_warranty_km": 5000}\'::json WHERE settings_snapshot IS NULL'
    )
    op.execute(
        """UPDATE purchase_request_lines l SET received_quantity=l.quantity
        FROM purchase_requests r WHERE l.purchase_request_id=r.id AND r.status::text IN ('RECIBIDA','CONCILIADA','recibida','conciliada')"""
    )
    # Keep legacy permission rows for rollback; explicit Ajustes permissions take precedence.
    for table, owner in [
        ("role_module_permissions", "role_id"),
        ("user_module_permissions", "user_id"),
    ]:
        op.execute(f"""INSERT INTO {table} (id,{owner},module_id,access)
            SELECT gen_random_uuid(),{owner},'ajustes',access FROM {table} WHERE module_id='post-ventas'
            ON CONFLICT ({owner},module_id) DO NOTHING""")


def downgrade():
    op.drop_table("stock_out_reasons")
    op.drop_table("warehouse_transfer_lot_allocations")
    for table, columns in reversed(list(COLUMNS.items())):
        for column in reversed(columns):
            op.drop_column(table, column.name)
