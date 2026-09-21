"""an upsell is now built from real tempario tasks and parts (so it carries
an amount), and approving it records who approved it and through what
channel

Revision ID: a870a2384320
Revises: 25aaa36f509c
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a870a2384320"
down_revision: str | Sequence[str] | None = "25aaa36f509c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    channel_enum = postgresql.ENUM(
        "whatsapp", "llamada", "correo", "sms", "presencial", name="upsell_approval_channel"
    )
    channel_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "upsells", sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("upsells", sa.Column("approval_channel", channel_enum, nullable=True))
    op.create_foreign_key(
        "fk_upsells_approved_by_user_id", "upsells", "users",
        ["approved_by_user_id"], ["id"], ondelete="SET NULL",
    )

    op.create_table(
        "upsell_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("upsell_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("upsells.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tempario_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("temparios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("code_snapshot", sa.String(20), nullable=False),
        sa.Column("name_snapshot", sa.String(150), nullable=False),
        sa.Column("hours_snapshot", sa.Numeric(6, 2), nullable=False),
    )
    op.create_index("ix_upsell_tasks_upsell_id", "upsell_tasks", ["upsell_id"])

    op.create_table(
        "upsell_parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("upsell_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("upsells.id", ondelete="CASCADE"), nullable=False),
        sa.Column("part_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("parts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name_snapshot", sa.String(150), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("unit_cost_snapshot", sa.Numeric(10, 2), nullable=False),
    )
    op.create_index("ix_upsell_parts_upsell_id", "upsell_parts", ["upsell_id"])


def downgrade() -> None:
    op.drop_index("ix_upsell_parts_upsell_id", table_name="upsell_parts")
    op.drop_table("upsell_parts")
    op.drop_index("ix_upsell_tasks_upsell_id", table_name="upsell_tasks")
    op.drop_table("upsell_tasks")

    op.drop_constraint("fk_upsells_approved_by_user_id", "upsells", type_="foreignkey")
    op.drop_column("upsells", "approval_channel")
    op.drop_column("upsells", "approved_by_user_id")
    op.execute("DROP TYPE IF EXISTS upsell_approval_channel")
