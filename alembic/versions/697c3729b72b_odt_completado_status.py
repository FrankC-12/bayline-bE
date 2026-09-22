"""an ODT ("Marcar como Pedido" from a service order) gains a third status,
Completado, set when almacén confirms the parts were physically handed
over — lets the elapsed-time counter running since Pedido pause instead of
counting forever

Revision ID: 697c3729b72b
Revises: cabb9ea2793c
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "697c3729b72b"
down_revision: str | Sequence[str] | None = "cabb9ea2793c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE transfer_status ADD VALUE IF NOT EXISTS 'COMPLETADO'")
    op.add_column(
        "service_order_transfers",
        sa.Column("completed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "service_order_transfers", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_service_order_transfers_completed_by_user_id", "service_order_transfers", "users",
        ["completed_by_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_service_order_transfers_completed_by_user_id", "service_order_transfers", type_="foreignkey"
    )
    op.drop_column("service_order_transfers", "completed_at")
    op.drop_column("service_order_transfers", "completed_by_user_id")
