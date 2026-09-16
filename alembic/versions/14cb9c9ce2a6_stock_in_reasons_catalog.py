"""holding-wide stock-in reasons catalog (motivos de entrada)

Revision ID: 14cb9c9ce2a6
Revises: 5afdece122a8
Create Date: 2026-09-16
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "14cb9c9ce2a6"
down_revision: str | Sequence[str] | None = "5afdece122a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Preloaded for every holding — new ones at creation time
# (AlmacenService.seed_default_stock_in_reasons), existing ones backfilled
# here. Matches the two hardcoded defaults this catalog replaces (the
# frontend's old localStorage list, and StockInCreate's schema default).
DEFAULT_STOCK_IN_REASONS = [
    "Compra directa",
    "Ajuste de inventario",
    "Devolución de cliente",
    "Otro",
]


def upgrade() -> None:
    op.create_table(
        "stock_in_reasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("holding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["holding_id"], ["holdings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_stock_in_reasons_holding_id", "stock_in_reasons", ["holding_id"])

    connection = op.get_bind()
    holding_ids = [row[0] for row in connection.execute(sa.text("SELECT id FROM holdings")).fetchall()]
    insert_reason = sa.text(
        "INSERT INTO stock_in_reasons (id, holding_id, name, is_active) "
        "VALUES (:id, :holding_id, :name, true)"
    )
    for holding_id in holding_ids:
        for name in DEFAULT_STOCK_IN_REASONS:
            connection.execute(insert_reason, {"id": str(uuid.uuid4()), "holding_id": str(holding_id), "name": name})


def downgrade() -> None:
    op.drop_index("ix_stock_in_reasons_holding_id", table_name="stock_in_reasons")
    op.drop_table("stock_in_reasons")
