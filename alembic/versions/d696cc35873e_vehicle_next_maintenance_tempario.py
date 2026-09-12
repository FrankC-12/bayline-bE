"""vehicle next maintenance tempario

Revision ID: d696cc35873e
Revises: c1bfd55a9fe5
Create Date: 2026-09-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "d696cc35873e"
down_revision = "c1bfd55a9fe5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vehicles",
        sa.Column("next_maintenance_tempario_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_vehicles_next_maintenance_tempario_id_temparios",
        "vehicles",
        "temparios",
        ["next_maintenance_tempario_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "fk_vehicles_next_maintenance_tempario_id_temparios", "vehicles", type_="foreignkey"
    )
    op.drop_column("vehicles", "next_maintenance_tempario_id")
