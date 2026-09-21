"""a vehicle can be saved without a plate ("sin placa") — e.g. one just
bought and not yet registered — and an existing plate is no longer
re-validated on every save unless it's actually being changed

Revision ID: 080f5c496223
Revises: c311861d105c
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "080f5c496223"
down_revision: str | Sequence[str] | None = "c311861d105c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("vehicles", "plate", existing_type=sa.String(length=8), nullable=True)


def downgrade() -> None:
    op.alter_column("vehicles", "plate", existing_type=sa.String(length=8), nullable=False)
