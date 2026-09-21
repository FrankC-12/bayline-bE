"""parts counter sales now carry a real tax breakdown — IVA on the subtotal
and IGTF on (subtotal + IVA), the same formula already used for vehicles and
service orders, frozen at sale time from the filial's LaborSettings

Revision ID: d397741035ff
Revises: a870a2384320
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d397741035ff"
down_revision: str | Sequence[str] | None = "a870a2384320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "part_sales", sa.Column("iva_percentage", sa.Numeric(5, 2), nullable=False, server_default="0")
    )
    op.add_column(
        "part_sales", sa.Column("iva_amount", sa.Numeric(12, 2), nullable=False, server_default="0")
    )
    op.add_column(
        "part_sales", sa.Column("igtf_percentage", sa.Numeric(5, 2), nullable=False, server_default="0")
    )
    op.add_column(
        "part_sales", sa.Column("igtf_amount", sa.Numeric(12, 2), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("part_sales", "igtf_amount")
    op.drop_column("part_sales", "igtf_percentage")
    op.drop_column("part_sales", "iva_amount")
    op.drop_column("part_sales", "iva_percentage")
