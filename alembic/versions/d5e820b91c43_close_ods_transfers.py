"""Close workshop requests when their service order is delivered."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "d5e820b91c43"
down_revision = "c4d719a80b32"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "service_order_transfers", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "service_order_transfers",
        sa.Column(
            "closed_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Correct existing closed orders without manufacturing stock movements or
    # pickup evidence. Their original closing actor was not recorded here.
    op.execute("""UPDATE service_order_transfers AS request
        SET closed_at = COALESCE(ods.closed_at, ods.updated_at, ods.created_at)
        FROM service_orders AS ods
        WHERE ods.id = request.service_order_id AND ods.status = 'ORDEN_CERRADA'""")
    op.execute("""UPDATE warehouse_transfers AS relocation
        SET status = 'CANCELADA', note = COALESCE(relocation.note || E'\\n', '') || 'Cierre automático: ODS ya cerrada.'
        FROM service_order_transfers AS request
        WHERE relocation.workshop_request_id = request.id AND request.closed_at IS NOT NULL
          AND relocation.status IN ('PEDIDO', 'EN_PROCESO')""")


def downgrade():
    # Reopening orders or cancelled stock requests would invent an operational
    # action. Only remove the added audit fields; retain cancellation history.
    op.drop_column("service_order_transfers", "closed_by_user_id")
    op.drop_column("service_order_transfers", "closed_at")
