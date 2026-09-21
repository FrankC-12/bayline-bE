"""a client can now stand in for a Supplier as an ODS billing target (the
same way one already stands in for the holding via is_holding_billing), so
"facturar a otro cliente" can bill a real supplier by name/RIF

Revision ID: c2da052f1bc8
Revises: f31904f51c61
Create Date: 2026-09-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c2da052f1bc8"
down_revision: str | Sequence[str] | None = "f31904f51c61"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients", sa.Column("linked_supplier_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_clients_linked_supplier_id", "clients", "suppliers",
        ["linked_supplier_id"], ["id"], ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_clients_linked_supplier_id", "clients", ["linked_supplier_id"])


def downgrade() -> None:
    op.drop_constraint("uq_clients_linked_supplier_id", "clients", type_="unique")
    op.drop_constraint("fk_clients_linked_supplier_id", "clients", type_="foreignkey")
    op.drop_column("clients", "linked_supplier_id")
