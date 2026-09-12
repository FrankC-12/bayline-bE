"""bill a different client than the vehicle owner + accounts receivable

Revision ID: 7f4a1c9e6b23
Revises: 9d3a2e7c5b41
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "7f4a1c9e6b23"
down_revision: str | Sequence[str] | None = "9d3a2e7c5b41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("is_holding_billing", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.add_column("service_order_invoices", sa.Column("billed_client_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("service_order_invoices", sa.Column("client_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("service_order_invoices", sa.Column("client_confirmed_note", sa.String(200), nullable=True))
    op.add_column("service_order_invoices", sa.Column("amount_paid_at_issuance", sa.Numeric(18, 2), nullable=True))
    op.add_column("service_order_invoices", sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("service_order_invoices", sa.Column("withholding_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("service_order_invoices", sa.Column("net_collected_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("service_order_invoices", sa.Column("collection_account_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("service_order_invoices", sa.Column("collection_income_entry_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("service_order_invoices", sa.Column("collected_by_user_id", postgresql.UUID(as_uuid=True), nullable=True))

    # Backfill existing invoices: billed the vehicle's own owner, paid in full
    # at issuance, already collected — so none retroactively show up as AR.
    op.execute(
        """
        UPDATE service_order_invoices soi
        SET billed_client_id = c.id,
            amount_paid_at_issuance = soi.total_usd,
            collected_at = soi.issued_at
        FROM service_orders so
        JOIN vehicles v ON v.id = so.vehicle_id
        JOIN clients c ON c.id = v.client_id
        WHERE so.id = soi.service_order_id
        """
    )

    op.alter_column("service_order_invoices", "billed_client_id", nullable=False)
    op.alter_column("service_order_invoices", "amount_paid_at_issuance", nullable=False)

    op.create_foreign_key(
        "fk_service_order_invoices_billed_client_id",
        "service_order_invoices", "clients", ["billed_client_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_service_order_invoices_collection_account_id",
        "service_order_invoices", "accounts", ["collection_account_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_service_order_invoices_collection_income_entry_id",
        "service_order_invoices", "income_entries", ["collection_income_entry_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_service_order_invoices_collected_by_user_id",
        "service_order_invoices", "users", ["collected_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_service_order_invoices_collected_by_user_id", "service_order_invoices", type_="foreignkey")
    op.drop_constraint("fk_service_order_invoices_collection_income_entry_id", "service_order_invoices", type_="foreignkey")
    op.drop_constraint("fk_service_order_invoices_collection_account_id", "service_order_invoices", type_="foreignkey")
    op.drop_constraint("fk_service_order_invoices_billed_client_id", "service_order_invoices", type_="foreignkey")

    op.drop_column("service_order_invoices", "collected_by_user_id")
    op.drop_column("service_order_invoices", "collection_income_entry_id")
    op.drop_column("service_order_invoices", "collection_account_id")
    op.drop_column("service_order_invoices", "net_collected_amount")
    op.drop_column("service_order_invoices", "withholding_amount")
    op.drop_column("service_order_invoices", "collected_at")
    op.drop_column("service_order_invoices", "amount_paid_at_issuance")
    op.drop_column("service_order_invoices", "client_confirmed_note")
    op.drop_column("service_order_invoices", "client_confirmed_at")
    op.drop_column("service_order_invoices", "billed_client_id")

    op.drop_column("clients", "is_holding_billing")
