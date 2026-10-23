"""Preserve mobile intake fluid readings."""

import sqlalchemy as sa

from alembic import op

revision = "f7a042db3e65"
down_revision = "e6f931ca2d54"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "preliminary_inspections",
        sa.Column("fluids", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("preliminary_inspections", "fluids")
