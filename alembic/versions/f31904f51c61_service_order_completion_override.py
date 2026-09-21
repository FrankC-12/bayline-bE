"""marking a service order as completado while it still has an unfinished
task or an undispatched ODT now requires an explicit confirmation, recorded
with who confirmed it and when

Revision ID: f31904f51c61
Revises: d397741035ff
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f31904f51c61"
down_revision: str | Sequence[str] | None = "d397741035ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_orders",
        sa.Column("completed_with_pending_items", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "service_orders",
        sa.Column("completed_override_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "service_orders", sa.Column("completed_override_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_service_orders_completed_override_by_user_id", "service_orders", "users",
        ["completed_override_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_service_orders_completed_override_by_user_id", "service_orders", type_="foreignkey")
    op.drop_column("service_orders", "completed_override_at")
    op.drop_column("service_orders", "completed_override_by_user_id")
    op.drop_column("service_orders", "completed_with_pending_items")
