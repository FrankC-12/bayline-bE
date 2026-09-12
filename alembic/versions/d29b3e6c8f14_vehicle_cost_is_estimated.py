"""dealership_vehicles.cost_is_estimated — flags whether a vehicle's cost
was an actual acquisition cost or a stand-in guess, since there's no vehicle
purchase-record subsystem to derive it from automatically.

Revision ID: d29b3e6c8f14
Revises: c4a8f1d92e67
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d29b3e6c8f14"
down_revision: str | Sequence[str] | None = "c4a8f1d92e67"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dealership_vehicles",
        sa.Column("cost_is_estimated", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("dealership_vehicles", "cost_is_estimated")
