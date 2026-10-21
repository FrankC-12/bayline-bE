"""Shared API rate-limit budgets."""

import sqlalchemy as sa

from alembic import op

revision = "e6f931ca2d54"
down_revision = "d5e820b91c43"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_rate_buckets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_api_rate_buckets_expires_at", "api_rate_buckets", ["expires_at"])


def downgrade():
    op.drop_table("api_rate_buckets")
