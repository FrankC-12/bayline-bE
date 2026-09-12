"""new rework failure categories (error_diagnostico, mal_uso_cliente,
no_determinada) — in their own migration since Postgres requires new enum
values to be committed before use

Revision ID: b7f3a9d2c841
Revises: a1d4e7c9f230
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7f3a9d2c841"
down_revision: str | Sequence[str] | None = "a1d4e7c9f230"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Old Postgres enum values can't be dropped without recreating the type,
    # so DESGASTE_NORMAL/OTRO stay defined but unused going forward —
    # existing rows get remapped in the next migration.
    op.execute("ALTER TYPE rework_failure_category ADD VALUE IF NOT EXISTS 'ERROR_DIAGNOSTICO'")
    op.execute("ALTER TYPE rework_failure_category ADD VALUE IF NOT EXISTS 'MAL_USO_CLIENTE'")
    op.execute("ALTER TYPE rework_failure_category ADD VALUE IF NOT EXISTS 'NO_DETERMINADA'")


def downgrade() -> None:
    pass
