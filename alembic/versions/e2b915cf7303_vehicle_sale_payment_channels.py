"""Record dealership sale payment channels.

Revision ID: e2b915cf7303
Revises: d1a804be6202
"""

from alembic import op
import sqlalchemy as sa

revision = "e2b915cf7303"
down_revision = "d1a804be6202"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind in [
        ("engine_type", sa.String(100)),
        ("seats", sa.Integer()),
        ("upholstery", sa.String(60)),
        ("previous_owners", sa.Integer()),
    ]:
        op.add_column("dealership_vehicles", sa.Column(name, kind, nullable=True))
    op.add_column(
        "dealership_vehicles",
        sa.Column("registration_documents", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("vehicle_sales", sa.Column("vehicle_snapshot", sa.JSON(), nullable=True))
    op.add_column("vehicle_sales", sa.Column("usd_payment_channel", sa.String(20), nullable=True))
    op.add_column("vehicle_sales", sa.Column("bs_payment_channel", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("vehicle_sales", "vehicle_snapshot")
    for name in ("registration_documents", "previous_owners", "upholstery", "seats", "engine_type"):
        op.drop_column("dealership_vehicles", name)
    op.drop_column("vehicle_sales", "bs_payment_channel")
    op.drop_column("vehicle_sales", "usd_payment_channel")
