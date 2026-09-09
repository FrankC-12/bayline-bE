"""Track the value date of labor_settings.bcv_rate so it can be synced
from the exchange_rates scraper and flagged stale when out of date.

Revision ID: 58078b83ff6b
Revises: a4d37e5fb243
"""

import sqlalchemy as sa

from alembic import op

revision = "58078b83ff6b"
down_revision = "a4d37e5fb243"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("labor_settings", sa.Column("bcv_rate_date", sa.Date(), nullable=True))


def downgrade():
    op.drop_column("labor_settings", "bcv_rate_date")
