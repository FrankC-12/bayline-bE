"""S3 · Transferencias entre cuentas — ACCOUNT_TRANSFER source_type +
TRANSFERENCIA_CUENTAS expense category, so a transfer between two of the
filial's own accounts can be tagged and excluded from Ingresos, Egresos and
Rentabilidad. No new columns — reuses IncomeEntry/ExpenseEntry's existing
source_type/source_id pointer.

Revision ID: 501e255c8dd4
Revises: f4c1a9e6d2b8
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "501e255c8dd4"
down_revision: str | Sequence[str] | None = "f4c1a9e6d2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE movement_source_type ADD VALUE IF NOT EXISTS 'ACCOUNT_TRANSFER'")
    op.execute("ALTER TYPE expense_category ADD VALUE IF NOT EXISTS 'TRANSFERENCIA_CUENTAS'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; downgrading this type is a no-op,
    # matching every other enum-value-add migration in this codebase.
    pass
