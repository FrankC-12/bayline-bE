"""Invalidate all access tokens when a user is suspended."""

from alembic import op
import sqlalchemy as sa

revision = "a4db37e95215"
down_revision = "f3ca26d84104"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade():
    op.drop_column("users", "session_version")
