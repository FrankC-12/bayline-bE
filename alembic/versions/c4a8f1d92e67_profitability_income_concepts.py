"""income_concept gains garantia_marca / fi_intermediacion — manual income
concepts for manufacturer-warranty and F&I revenue, reusing the existing
manual-entry form rather than building dedicated billing workflows for them.

Revision ID: c4a8f1d92e67
Revises: b7e2c9a14f83
Create Date: 2026-09-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4a8f1d92e67"
down_revision: str | Sequence[str] | None = "b7e2c9a14f83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE income_concept ADD VALUE IF NOT EXISTS 'GARANTIA_MARCA'")
    op.execute("ALTER TYPE income_concept ADD VALUE IF NOT EXISTS 'FI_INTERMEDIACION'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; downgrading this type is a no-op,
    # matching every other enum-value-add migration in this codebase.
    pass
