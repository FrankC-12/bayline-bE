"""fix movement_type enum casing

Revision ID: 6f3f68db6f9b
Revises: 5f7d1924fd32
Create Date: 2026-08-27 16:17:42.527279

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f3f68db6f9b'
down_revision: Union[str, Sequence[str], None] = '5f7d1924fd32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'SALIDA'")
    op.execute("ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'DEVOLUCION'")


def downgrade() -> None:
    # Postgres no permite quitar valores de un enum fácilmente; no-op.
    pass
