"""manual movement controls — concept, counterparty, frozen exchange rate,
attachment, reversal-only correction for income/expense entries; a new
attachment threshold setting

Revision ID: a1f4c7e29b56
Revises: e9a2c5f8b364
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1f4c7e29b56"
down_revision: str | Sequence[str] | None = "e9a2c5f8b364"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    income_concept = postgresql.ENUM(
        "COBRO_CLIENTE", "REEMBOLSO_HOLDING", "APORTE_SOCIO", "VENTA_ACTIVO", "OTRO_INGRESO",
        name="income_concept",
    )
    income_concept.create(op.get_bind(), checkfirst=True)

    counterparty_type = postgresql.ENUM(
        "CLIENTE", "PROVEEDOR", "TERCERO", "SOCIO", name="counterparty_type"
    )
    counterparty_type.create(op.get_bind(), checkfirst=True)

    for table in ("income_entries", "expense_entries"):
        if table == "income_entries":
            op.add_column(table, sa.Column("concept", income_concept, nullable=True))
        op.add_column(table, sa.Column("counterparty_type", counterparty_type, nullable=True))
        op.add_column(table, sa.Column("counterparty_client_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.add_column(table, sa.Column("counterparty_supplier_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.add_column(table, sa.Column("counterparty_name", sa.String(150), nullable=True))
        op.add_column(table, sa.Column("reference", sa.String(60), nullable=True))
        op.add_column(table, sa.Column("exchange_rate", sa.Numeric(18, 8), nullable=True))
        op.add_column(table, sa.Column("amount_usd", sa.Numeric(12, 2), nullable=True))
        op.add_column(table, sa.Column("amount_bs", sa.Numeric(18, 2), nullable=True))
        op.add_column(table, sa.Column("attachment_url", sa.String(500), nullable=True))
        op.add_column(table, sa.Column("reverses_entry_id", postgresql.UUID(as_uuid=True), nullable=True))

        op.create_foreign_key(
            f"fk_{table}_counterparty_client_id", table, "clients", ["counterparty_client_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            f"fk_{table}_counterparty_supplier_id", table, "suppliers", ["counterparty_supplier_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            f"fk_{table}_reverses_entry_id", table, table, ["reverses_entry_id"], ["id"], ondelete="SET NULL",
        )

    op.add_column(
        "labor_settings",
        sa.Column(
            "manual_movement_attachment_threshold_usd", sa.Numeric(10, 2), nullable=False, server_default="100"
        ),
    )


def downgrade() -> None:
    op.drop_column("labor_settings", "manual_movement_attachment_threshold_usd")

    for table in ("income_entries", "expense_entries"):
        op.drop_constraint(f"fk_{table}_reverses_entry_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_counterparty_supplier_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_counterparty_client_id", table, type_="foreignkey")

        op.drop_column(table, "reverses_entry_id")
        op.drop_column(table, "attachment_url")
        op.drop_column(table, "amount_bs")
        op.drop_column(table, "amount_usd")
        op.drop_column(table, "exchange_rate")
        op.drop_column(table, "reference")
        op.drop_column(table, "counterparty_name")
        op.drop_column(table, "counterparty_supplier_id")
        op.drop_column(table, "counterparty_client_id")
        op.drop_column(table, "counterparty_type")
        if table == "income_entries":
            op.drop_column(table, "concept")

    op.execute("DROP TYPE IF EXISTS counterparty_type")
    op.execute("DROP TYPE IF EXISTS income_concept")
