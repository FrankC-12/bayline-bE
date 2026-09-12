"""warranty submissions (monthly presentación to the holding)

Revision ID: 9d3a2e7c5b41
Revises: 2b8f6a1c4d90
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9d3a2e7c5b41"
down_revision: str | Sequence[str] | None = "2b8f6a1c4d90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        "BORRADOR", "PRESENTADA", "PAGADA", name="warranty_submission_status", create_type=False
    )
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "warranty_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("currency", postgresql.ENUM(name="account_currency", create_type=False), nullable=False),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("withholding_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("net_amount_received", sa.Numeric(12, 2), nullable=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("income_entry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["paid_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["income_entry_id"], ["income_entries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filial_id", "period_year", "period_month", "currency", name="uq_warranty_submission_period"),
    )
    op.create_index("ix_warranty_submissions_filial_id", "warranty_submissions", ["filial_id"])

    op.add_column("supplier_claims", sa.Column("warranty_submission_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_supplier_claims_warranty_submission_id",
        "supplier_claims",
        "warranty_submissions",
        ["warranty_submission_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_supplier_claims_warranty_submission_id", "supplier_claims", type_="foreignkey")
    op.drop_column("supplier_claims", "warranty_submission_id")

    op.drop_index("ix_warranty_submissions_filial_id", table_name="warranty_submissions")
    op.drop_table("warranty_submissions")
    postgresql.ENUM(name="warranty_submission_status").drop(op.get_bind(), checkfirst=True)
