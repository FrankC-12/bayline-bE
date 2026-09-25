"""task status expansion

Revision ID: c7e2b5a1f930
Revises: f3a6c1d9e824
Create Date: 2026-09-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c7e2b5a1f930"
down_revision: str | Sequence[str] | None = "f3a6c1d9e824"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'EN_ESPERA_DE_REPUESTOS'")
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'EN_PROGRESO'")
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'CANCELADA'")


def downgrade() -> None:
    # Postgres can't drop enum values — a downgrade would need to recreate
    # the type from scratch, which isn't worth it for an additive change.
    pass
