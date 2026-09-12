"""maintenance plans

Revision ID: a77d06154229
Revises: d696cc35873e
Create Date: 2026-09-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a77d06154229"
down_revision = "d696cc35873e"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "maintenance_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filial_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filiales.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("brand", sa.String(60), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_maintenance_plans_filial_id", "maintenance_plans", ["filial_id"])

    op.create_table(
        "maintenance_plan_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("maintenance_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tempario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("temparios.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("interval_km", sa.Integer(), nullable=True),
        sa.Column("interval_months", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_maintenance_plan_entries_plan_id", "maintenance_plan_entries", ["plan_id"]
    )


def downgrade():
    op.drop_index("ix_maintenance_plan_entries_plan_id", table_name="maintenance_plan_entries")
    op.drop_table("maintenance_plan_entries")
    op.drop_index("ix_maintenance_plans_filial_id", table_name="maintenance_plans")
    op.drop_table("maintenance_plans")
