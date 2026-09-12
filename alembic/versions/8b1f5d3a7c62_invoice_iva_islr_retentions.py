"""IVA/ISLR withholding on service order invoices + filial default percentages

Revision ID: 8b1f5d3a7c62
Revises: 4c7e2a91d6f0
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8b1f5d3a7c62"
down_revision: str | Sequence[str] | None = "4c7e2a91d6f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "labor_settings",
        sa.Column("iva_retention_default_percentage", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "labor_settings",
        sa.Column("islr_retention_default_percentage", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )

    op.add_column("service_order_invoices", sa.Column("iva_retention_percentage", sa.Numeric(5, 2), nullable=True))
    op.add_column("service_order_invoices", sa.Column("iva_retention_amount", sa.Numeric(18, 2), nullable=True))
    op.add_column("service_order_invoices", sa.Column("islr_retention_percentage", sa.Numeric(5, 2), nullable=True))
    op.add_column("service_order_invoices", sa.Column("islr_retention_amount", sa.Numeric(18, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("service_order_invoices", "islr_retention_amount")
    op.drop_column("service_order_invoices", "islr_retention_percentage")
    op.drop_column("service_order_invoices", "iva_retention_amount")
    op.drop_column("service_order_invoices", "iva_retention_percentage")

    op.drop_column("labor_settings", "islr_retention_default_percentage")
    op.drop_column("labor_settings", "iva_retention_default_percentage")
