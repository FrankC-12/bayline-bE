"""Capture reception data on service order creation.

Revision ID: b512cca98fe5
Revises: d8edae7a757a
"""

import sqlalchemy as sa

from alembic import op

revision = "b512cca98fe5"
down_revision = "d8edae7a757a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("service_orders", sa.Column("intake_mileage", sa.Integer(), nullable=True))
    op.add_column("service_orders", sa.Column("customer_reason", sa.Text(), nullable=True))
    op.add_column("service_orders", sa.Column("promised_at", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("service_orders", "promised_at")
    op.drop_column("service_orders", "customer_reason")
    op.drop_column("service_orders", "intake_mileage")
