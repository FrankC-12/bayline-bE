"""Persistent retries for inventory movements and parts-sale collections."""

import sqlalchemy as sa

from alembic import op

revision = "e2a5c81b7d40"
down_revision = "d1e84a790b62"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operation_receipts",
        sa.Column("request_id", sa.UUID(), primary_key=True),
        sa.Column(
            "filial_id", sa.UUID(), sa.ForeignKey("filiales.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("operation", sa.String(60), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade():
    op.drop_table("operation_receipts")
