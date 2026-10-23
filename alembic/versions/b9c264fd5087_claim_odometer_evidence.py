"""Store explicit odometer evidence for warranty claims."""

import sqlalchemy as sa

from alembic import op

revision = "b9c264fd5087"
down_revision = "a8b153ec4f76"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "warranty_claims",
        sa.Column("failure_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "warranty_claims", sa.Column("odometer_photo_url", sa.String(1000), nullable=True)
    )


def downgrade():
    op.drop_column("warranty_claims", "failure_confirmed")
    op.drop_column("warranty_claims", "odometer_photo_url")
