"""Dedicated mobile detailed inspections, one per service order."""

import sqlalchemy as sa

from alembic import op

revision = "d1e84a790b62"
down_revision = "c9f672a4d813"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "detailed_inspections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "service_order_id",
            sa.UUID(),
            sa.ForeignKey("service_orders.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("annexes", sa.JSON(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "updated_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "completed_by_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT")
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade():
    op.drop_table("detailed_inspections")
