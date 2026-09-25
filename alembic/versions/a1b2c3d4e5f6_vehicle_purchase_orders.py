"""vehicle purchase orders (compras)

Revision ID: a1b2c3d4e5f6
Revises: f2c8a4d0e6b1
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f2c8a4d0e6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicle_purchase_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ENVIADA", "PARCIALMENTE_RECIBIDA", "RECIBIDA", "CONCILIADA", "CANCELADA",
                name="vehicle_purchase_order_status",
            ),
            nullable=False,
            server_default="ENVIADA",
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicle_purchase_orders_filial_id", "vehicle_purchase_orders", ["filial_id"])

    op.create_table(
        "vehicle_purchase_order_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand", sa.String(60), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("version", sa.String(60), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("color", sa.String(40), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["vehicle_purchase_orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vehicle_purchase_order_lines_purchase_order_id", "vehicle_purchase_order_lines", ["purchase_order_id"]
    )

    op.create_table(
        "vehicle_purchase_order_receptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("received_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["vehicle_purchase_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["received_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vehicle_purchase_order_receptions_purchase_order_id",
        "vehicle_purchase_order_receptions", ["purchase_order_id"],
    )

    op.create_table(
        "vehicle_purchase_order_invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchase_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invoice_number", sa.String(60), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("issued_at", sa.Date(), nullable=False),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["purchase_order_id"], ["vehicle_purchase_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_vehicle_purchase_order_invoices_purchase_order_id",
        "vehicle_purchase_order_invoices", ["purchase_order_id"],
    )

    op.add_column("dealership_vehicles", sa.Column("version", sa.String(60), nullable=True))
    op.add_column(
        "dealership_vehicles", sa.Column("purchase_order_line_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "dealership_vehicles", sa.Column("reception_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "dealership_vehicles", sa.Column("purchase_order_invoice_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_dealership_vehicles_purchase_order_line_id", "dealership_vehicles", "vehicle_purchase_order_lines",
        ["purchase_order_line_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_dealership_vehicles_reception_id", "dealership_vehicles", "vehicle_purchase_order_receptions",
        ["reception_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_dealership_vehicles_purchase_order_invoice_id", "dealership_vehicles", "vehicle_purchase_order_invoices",
        ["purchase_order_invoice_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_dealership_vehicles_purchase_order_invoice_id", "dealership_vehicles", type_="foreignkey")
    op.drop_constraint("fk_dealership_vehicles_reception_id", "dealership_vehicles", type_="foreignkey")
    op.drop_constraint("fk_dealership_vehicles_purchase_order_line_id", "dealership_vehicles", type_="foreignkey")
    op.drop_column("dealership_vehicles", "purchase_order_invoice_id")
    op.drop_column("dealership_vehicles", "reception_id")
    op.drop_column("dealership_vehicles", "purchase_order_line_id")
    op.drop_column("dealership_vehicles", "version")

    op.drop_index("ix_vehicle_purchase_order_invoices_purchase_order_id", table_name="vehicle_purchase_order_invoices")
    op.drop_table("vehicle_purchase_order_invoices")
    op.drop_index(
        "ix_vehicle_purchase_order_receptions_purchase_order_id", table_name="vehicle_purchase_order_receptions"
    )
    op.drop_table("vehicle_purchase_order_receptions")
    op.drop_index("ix_vehicle_purchase_order_lines_purchase_order_id", table_name="vehicle_purchase_order_lines")
    op.drop_table("vehicle_purchase_order_lines")
    op.drop_index("ix_vehicle_purchase_orders_filial_id", table_name="vehicle_purchase_orders")
    op.drop_table("vehicle_purchase_orders")
    op.execute("DROP TYPE IF EXISTS vehicle_purchase_order_status")
