"""payer per service order line — who pays for each task/transfer line
(cliente, garantia_taller, garantia_fabrica, plan_mantenimiento, proveedor)

Revision ID: b6d9f2a4c817
Revises: a3c8e15f7d24
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b6d9f2a4c817"
down_revision: str | Sequence[str] | None = "a3c8e15f7d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    payer_enum = postgresql.ENUM(
        "CLIENTE",
        "GARANTIA_TALLER",
        "GARANTIA_FABRICA",
        "PLAN_MANTENIMIENTO",
        "PROVEEDOR",
        name="service_order_payer",
        create_type=False,
    )
    payer_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "service_order_tasks",
        sa.Column("payer", payer_enum, nullable=False, server_default="CLIENTE"),
    )
    op.add_column(
        "service_order_transfer_lines",
        sa.Column("payer", payer_enum, nullable=False, server_default="CLIENTE"),
    )


def downgrade() -> None:
    op.drop_column("service_order_transfer_lines", "payer")
    op.drop_column("service_order_tasks", "payer")
    op.execute("DROP TYPE IF EXISTS service_order_payer")
