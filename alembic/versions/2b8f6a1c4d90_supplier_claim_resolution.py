"""supplier claim resolution (rejected importer invoice)

Revision ID: 2b8f6a1c4d90
Revises: 877900798cac
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2b8f6a1c4d90"
down_revision: str | Sequence[str] | None = "877900798cac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'GARANTIA_RECHAZADA'")

    claim_resolution = postgresql.ENUM(
        "CARGO_CLIENTE", "COSTO_TALLER", name="claim_resolution", create_type=False
    )
    claim_resolution.create(op.get_bind(), checkfirst=True)

    op.add_column("supplier_claims", sa.Column("claimed_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "supplier_claims",
        sa.Column("currency", postgresql.ENUM(name="account_currency", create_type=False), nullable=True),
    )
    op.add_column("supplier_claims", sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("supplier_claims", sa.Column("resolution", claim_resolution, nullable=True))
    op.add_column("supplier_claims", sa.Column("resolution_note", sa.Text(), nullable=True))
    op.add_column(
        "supplier_claims", sa.Column("resolved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("supplier_claims", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("supplier_claims", sa.Column("expense_entry_id", postgresql.UUID(as_uuid=True), nullable=True))

    op.create_foreign_key(
        "fk_supplier_claims_client_id", "supplier_claims", "clients", ["client_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_supplier_claims_resolved_by_user_id",
        "supplier_claims",
        "users",
        ["resolved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_supplier_claims_expense_entry_id",
        "supplier_claims",
        "expense_entries",
        ["expense_entry_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_supplier_claims_expense_entry_id", "supplier_claims", type_="foreignkey")
    op.drop_constraint("fk_supplier_claims_resolved_by_user_id", "supplier_claims", type_="foreignkey")
    op.drop_constraint("fk_supplier_claims_client_id", "supplier_claims", type_="foreignkey")

    op.drop_column("supplier_claims", "expense_entry_id")
    op.drop_column("supplier_claims", "resolved_at")
    op.drop_column("supplier_claims", "resolved_by_user_id")
    op.drop_column("supplier_claims", "resolution_note")
    op.drop_column("supplier_claims", "resolution")
    op.drop_column("supplier_claims", "client_id")
    op.drop_column("supplier_claims", "currency")
    op.drop_column("supplier_claims", "claimed_amount")

    op.execute("DROP TYPE IF EXISTS claim_resolution")
