"""selling a dealership vehicle below its cost_price now requires an
administracion-level override, with who authorized it and when recorded on
the sale

Revision ID: 25aaa36f509c
Revises: b809a17e3122
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "25aaa36f509c"
down_revision: str | Sequence[str] | None = "b809a17e3122"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vehicle_sales",
        sa.Column("below_cost_override", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("vehicle_sales", sa.Column("below_cost_override_note", sa.String(500), nullable=True))
    op.add_column(
        "vehicle_sales",
        sa.Column("authorized_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "vehicle_sales", sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_vehicle_sales_authorized_by_user_id", "vehicle_sales", "users",
        ["authorized_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_vehicle_sales_authorized_by_user_id", "vehicle_sales", type_="foreignkey")
    op.drop_column("vehicle_sales", "authorized_at")
    op.drop_column("vehicle_sales", "authorized_by_user_id")
    op.drop_column("vehicle_sales", "below_cost_override_note")
    op.drop_column("vehicle_sales", "below_cost_override")
