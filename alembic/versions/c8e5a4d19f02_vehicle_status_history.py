"""Keep actor, date, reason and reservation data for vehicle status changes."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c8e5a4d19f02"
down_revision = "b6d2e8f4c901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dealership_vehicle_status_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vehicle_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dealership_vehicles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("previous_status", sa.String(30), nullable=True),
        sa.Column("new_status", sa.String(30), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("reservation_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_dealership_vehicle_status_events_vehicle_id",
        "dealership_vehicle_status_events",
        ["vehicle_id"],
    )


def downgrade() -> None:
    op.drop_table("dealership_vehicle_status_events")
