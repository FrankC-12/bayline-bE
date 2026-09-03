"""supplier accounts, BCV rates and vehicle pricing

Revision ID: c3f1a9d4e782
Revises: 7a84e3c612fd
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3f1a9d4e782"
down_revision: str | Sequence[str] | None = "7a84e3c612fd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE account_currency ADD VALUE IF NOT EXISTS 'EUR'")
    payment_method = postgresql.ENUM(
        "TRANSFERENCIA", "PAGO_MOVIL", "ZELLE", "EFECTIVO", "OTRO",
        name="supplier_payment_method", create_type=False,
    )
    payment_method.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "supplier_payment_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_method", payment_method, nullable=False),
        sa.Column("bank_name", sa.String(100), nullable=True),
        sa.Column("account_holder", sa.String(150), nullable=False),
        sa.Column("document", sa.String(20), nullable=True),
        sa.Column("account_number", sa.String(40), nullable=True),
        sa.Column("account_type", sa.String(30), nullable=True),
        sa.Column("currency", postgresql.ENUM(name="account_currency", create_type=False), nullable=False),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("email", sa.String(150), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_supplier_payment_accounts_supplier_id", "supplier_payment_accounts", ["supplier_id"])

    op.create_table(
        "exchange_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate_ves", sa.Numeric(18, 8), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("currency", "value_date", name="uq_exchange_rate_currency_date"),
    )
    op.create_index("ix_exchange_rates_currency", "exchange_rates", ["currency"])

    op.add_column("dealership_vehicles", sa.Column("price_currency", sa.String(3), server_default="USD", nullable=False))
    op.add_column("dealership_vehicles", sa.Column("iva_percentage", sa.Numeric(5, 2), server_default="16", nullable=False))
    op.add_column("dealership_vehicles", sa.Column("igtf_percentage", sa.Numeric(5, 2), server_default="3", nullable=False))
    op.add_column("dealership_vehicles", sa.Column("luxury_tax_percentage", sa.Numeric(5, 2), server_default="0", nullable=False))
    op.add_column("dealership_vehicles", sa.Column("financing_provider", sa.String(50), nullable=True))
    op.add_column("dealership_vehicles", sa.Column("financing_external_id", sa.String(100), nullable=True))


def downgrade() -> None:
    for column in (
        "financing_external_id", "financing_provider", "luxury_tax_percentage",
        "igtf_percentage", "iva_percentage", "price_currency",
    ):
        op.drop_column("dealership_vehicles", column)
    op.drop_index("ix_exchange_rates_currency", table_name="exchange_rates")
    op.drop_table("exchange_rates")
    op.drop_index("ix_supplier_payment_accounts_supplier_id", table_name="supplier_payment_accounts")
    op.drop_table("supplier_payment_accounts")
    postgresql.ENUM(name="supplier_payment_method").drop(op.get_bind(), checkfirst=True)
    # PostgreSQL enum values are intentionally retained; removing EUR would require rebuilding the type.
