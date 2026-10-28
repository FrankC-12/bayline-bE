"""Separate warehouse dispatch from counter reception.

Revision ID: f3ca26d84104
Revises: e2b915cf7303
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f3ca26d84104"
down_revision = "e2b915cf7303"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("created_by_user_id", "dispatched_by_user_id", "received_by_user_id"):
        op.add_column("part_sales", sa.Column(name, postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_part_sales_{name}", "part_sales", "users", [name], ["id"], ondelete="SET NULL"
        )
    op.add_column(
        "part_sales", sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("part_sales", "dispatched_at")
    for name in ("received_by_user_id", "dispatched_by_user_id", "created_by_user_id"):
        op.drop_constraint(f"fk_part_sales_{name}", "part_sales", type_="foreignkey")
        op.drop_column("part_sales", name)
