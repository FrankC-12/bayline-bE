"""Factory warranty distance and complete mobile interior evidence."""

import sqlalchemy as sa

from alembic import op

revision = "a8b153ec4f76"
down_revision = "f7a042db3e65"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "labor_settings", sa.Column("vehicle_warranty_default_km", sa.Integer(), nullable=True)
    )
    op.add_column(
        "preliminary_inspections",
        sa.Column("interior", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("preliminary_inspections", "interior")
    op.drop_column("labor_settings", "vehicle_warranty_default_km")
