"""Require photo evidence on part returns.

Revision ID: d8edae7a757a
Revises: 58078b83ff6b
"""

import sqlalchemy as sa

from alembic import op

revision = "d8edae7a757a"
down_revision = "58078b83ff6b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "part_returns",
        sa.Column("photo_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade():
    op.drop_column("part_returns", "photo_urls")
