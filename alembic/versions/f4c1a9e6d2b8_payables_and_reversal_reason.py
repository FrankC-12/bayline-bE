"""cuentas por pagar (purchase_requests payment tracking) + reversal reason

Revision ID: f4c1a9e6d2b8
Revises: e7a4c2f9b1d6
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4c1a9e6d2b8"
down_revision: str | Sequence[str] | None = "e7a4c2f9b1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "purchase_requests", sa.Column("conciliated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("purchase_requests", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "purchase_requests", sa.Column("payment_account_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "purchase_requests",
        sa.Column("payment_expense_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "purchase_requests", sa.Column("paid_by_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_purchase_requests_payment_account_id", "purchase_requests", "accounts",
        ["payment_account_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_purchase_requests_payment_expense_entry_id", "purchase_requests", "expense_entries",
        ["payment_expense_entry_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_purchase_requests_paid_by_user_id", "purchase_requests", "users",
        ["paid_by_user_id"], ["id"], ondelete="SET NULL",
    )

    op.add_column("income_entries", sa.Column("reversal_reason", sa.Text(), nullable=True))
    op.add_column("expense_entries", sa.Column("reversal_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("expense_entries", "reversal_reason")
    op.drop_column("income_entries", "reversal_reason")

    op.drop_constraint("fk_purchase_requests_paid_by_user_id", "purchase_requests", type_="foreignkey")
    op.drop_constraint(
        "fk_purchase_requests_payment_expense_entry_id", "purchase_requests", type_="foreignkey"
    )
    op.drop_constraint("fk_purchase_requests_payment_account_id", "purchase_requests", type_="foreignkey")
    op.drop_column("purchase_requests", "paid_by_user_id")
    op.drop_column("purchase_requests", "payment_expense_entry_id")
    op.drop_column("purchase_requests", "payment_account_id")
    op.drop_column("purchase_requests", "paid_at")
    op.drop_column("purchase_requests", "conciliated_at")
