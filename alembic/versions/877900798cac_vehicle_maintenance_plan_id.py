"""vehicle maintenance plan id

Revision ID: 877900798cac
Revises: a77d06154229
Create Date: 2026-09-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "877900798cac"
down_revision = "a77d06154229"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vehicles",
        sa.Column("maintenance_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_vehicles_maintenance_plan_id_maintenance_plans",
        "vehicles",
        "maintenance_plans",
        ["maintenance_plan_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "fk_vehicles_maintenance_plan_id_maintenance_plans", "vehicles", type_="foreignkey"
    )
    op.drop_column("vehicles", "maintenance_plan_id")
