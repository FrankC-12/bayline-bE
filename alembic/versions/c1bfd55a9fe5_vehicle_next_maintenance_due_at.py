"""vehicle next maintenance due at

Revision ID: c1bfd55a9fe5
Revises: c230c8f08717
Create Date: 2026-09-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c1bfd55a9fe5"
down_revision = "c230c8f08717"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "vehicles",
        sa.Column("next_maintenance_due_at", sa.Date(), nullable=True),
    )


def downgrade():
    op.drop_column("vehicles", "next_maintenance_due_at")
