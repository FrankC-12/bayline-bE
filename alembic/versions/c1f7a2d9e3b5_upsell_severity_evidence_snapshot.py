"""upsell severity, real photo evidence, frozen quoted price, discard reason

Revision ID: c1f7a2d9e3b5
Revises: b4d9e2f1a7c3
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c1f7a2d9e3b5"
down_revision: str | Sequence[str] | None = "b4d9e2f1a7c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    severity_enum = postgresql.ENUM("URGENTE", "PRONTO", "MONITOREAR", name="upsell_severity")
    severity_enum.create(op.get_bind(), checkfirst=True)
    discard_reason_enum = postgresql.ENUM(
        "YA_REPARADO_OTRO_TALLER", "CLIENTE_NO_LO_QUIERE", "YA_NO_APLICA", "OTRO",
        name="upsell_discard_reason",
    )
    discard_reason_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "upsells",
        sa.Column("severity", severity_enum, nullable=False, server_default="MONITOREAR"),
    )
    op.add_column("upsells", sa.Column("detected_mileage", sa.Integer(), nullable=True))
    op.add_column("upsells", sa.Column("quoted_price_snapshot", sa.Numeric(10, 2), nullable=True))
    op.add_column(
        "upsells", sa.Column("photo_urls", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "upsells", sa.Column("discard_reason", discard_reason_enum, nullable=True)
    )
    op.add_column("upsells", sa.Column("discard_note", sa.Text(), nullable=True))
    op.add_column(
        "upsells", sa.Column("applied_to_service_order_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_upsells_applied_to_service_order_id", "upsells", "service_orders",
        ["applied_to_service_order_id"], ["id"], ondelete="SET NULL",
    )
    op.drop_column("upsells", "evidence_count")


def downgrade() -> None:
    op.add_column(
        "upsells", sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.drop_constraint("fk_upsells_applied_to_service_order_id", "upsells", type_="foreignkey")
    op.drop_column("upsells", "applied_to_service_order_id")
    op.drop_column("upsells", "discard_note")
    op.drop_column("upsells", "discard_reason")
    op.drop_column("upsells", "photo_urls")
    op.drop_column("upsells", "quoted_price_snapshot")
    op.drop_column("upsells", "detected_mileage")
    op.drop_column("upsells", "severity")

    op.execute("DROP TYPE IF EXISTS upsell_discard_reason")
    op.execute("DROP TYPE IF EXISTS upsell_severity")
