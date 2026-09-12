"""workshop warranties — one per invoiced task, created automatically by
BillingService.issue, VIN-anchored so it survives a vehicle sale

Revision ID: c4e7a3f9d152
Revises: b6d9f2a4c817
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4e7a3f9d152"
down_revision: str | Sequence[str] | None = "b6d9f2a4c817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workshop_warranties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vin", sa.String(17), nullable=False),
        sa.Column("service_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_order_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tempario_code_snapshot", sa.String(20), nullable=False),
        sa.Column("tempario_name_snapshot", sa.String(150), nullable=False),
        sa.Column("technician_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("starts_at", sa.Date(), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("duration_km", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Date(), nullable=False),
        sa.Column("expiration_mileage", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_order_id"], ["service_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["service_order_task_id"], ["service_order_tasks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["technician_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("service_order_task_id", name="uq_workshop_warranty_task"),
    )
    op.create_index("ix_workshop_warranties_filial_id", "workshop_warranties", ["filial_id"])
    op.create_index("ix_workshop_warranties_vin", "workshop_warranties", ["vin"])


def downgrade() -> None:
    op.drop_table("workshop_warranties")
