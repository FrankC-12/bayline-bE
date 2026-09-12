"""income_entries/expense_entries.source_type + source_id — a polymorphic,
no-FK pointer back at whatever document generated an automatic entry
(a VehicleSale, PartSale, ServiceOrder, SupplierClaim, WarrantySubmission),
so the account-detail screen can open the real source instead of just
showing a description string.

Revision ID: b7e2c9a14f83
Revises: f3b6d1e8a742
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7e2c9a14f83"
down_revision: str | Sequence[str] | None = "f3b6d1e8a742"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    movement_source_type = postgresql.ENUM(
        "VEHICLE_SALE", "PART_SALE", "SERVICE_ORDER", "SUPPLIER_CLAIM", "WARRANTY_SUBMISSION",
        name="movement_source_type",
    )
    movement_source_type.create(op.get_bind(), checkfirst=True)

    for table in ("income_entries", "expense_entries"):
        op.add_column(table, sa.Column("source_type", movement_source_type, nullable=True))
        op.add_column(table, sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    for table in ("income_entries", "expense_entries"):
        op.drop_column(table, "source_id")
        op.drop_column(table, "source_type")

    op.execute("DROP TYPE IF EXISTS movement_source_type")
