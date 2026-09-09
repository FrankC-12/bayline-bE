"""Store immutable pricing breakdown when invoicing an order.

Revision ID: f3c26d4ea132
Revises: e2b15c3d9021
"""

import sqlalchemy as sa

from alembic import op

revision = "f3c26d4ea132"
down_revision = "e2b15c3d9021"
branch_labels = None
depends_on = None


def upgrade():
    # No historical backfill: current lines and settings cannot recreate an invoice.
    op.add_column("service_orders", sa.Column("pricing_snapshot", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("service_orders", "pricing_snapshot")
