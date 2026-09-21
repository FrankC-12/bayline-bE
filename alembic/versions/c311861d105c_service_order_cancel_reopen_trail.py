"""service orders: mandatory-reason cancellation and reopen, both leaving
who/when audit fields

Revision ID: c311861d105c
Revises: 7a1c4e9d2b83
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c311861d105c"
down_revision: str | Sequence[str] | None = "7a1c4e9d2b83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("service_orders", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column(
        "service_orders",
        sa.Column("cancelled_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "service_orders", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "service_orders",
        sa.Column("reopened_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "service_orders", sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_foreign_key(
        "fk_service_orders_cancelled_by_user_id", "service_orders", "users",
        ["cancelled_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_service_orders_reopened_by_user_id", "service_orders", "users",
        ["reopened_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_service_orders_reopened_by_user_id", "service_orders", type_="foreignkey")
    op.drop_constraint("fk_service_orders_cancelled_by_user_id", "service_orders", type_="foreignkey")

    op.drop_column("service_orders", "reopened_at")
    op.drop_column("service_orders", "reopened_by_user_id")
    op.drop_column("service_orders", "cancelled_at")
    op.drop_column("service_orders", "cancelled_by_user_id")
    op.drop_column("service_orders", "cancel_reason")
