"""service order type + warranty claim link

Revision ID: e51a7c93b048
Revises: d8b1f4a2c6e7
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e51a7c93b048"
down_revision: str | Sequence[str] | None = "d8b1f4a2c6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE service_order_type ADD VALUE IF NOT EXISTS 'GARANTIA_FABRICA'")
    op.execute("ALTER TYPE service_order_type ADD VALUE IF NOT EXISTS 'COMEBACK'")
    op.execute("ALTER TYPE service_order_type ADD VALUE IF NOT EXISTS 'CAMPANA'")
    op.add_column(
        "service_orders", sa.Column("warranty_claim_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_service_orders_warranty_claim_id", "service_orders", "warranty_claims",
        ["warranty_claim_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_service_orders_warranty_claim_id", "service_orders", type_="foreignkey")
    op.drop_column("service_orders", "warranty_claim_id")
