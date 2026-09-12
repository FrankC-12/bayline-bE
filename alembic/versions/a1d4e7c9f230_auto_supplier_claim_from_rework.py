"""trace part -> lot -> purchase order -> supplier; auto supplier claim from
a rework claim on a defective part (F0-01)

Revision ID: a1d4e7c9f230
Revises: 9c2e7f4b8a15
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1d4e7c9f230"
down_revision: str | Sequence[str] | None = "9c2e7f4b8a15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part A — a lot remembers which purchase it came from.
    op.add_column(
        "part_lots", sa.Column("purchase_request_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_part_lots_purchase_request_id",
        "part_lots", "purchase_requests", ["purchase_request_id"], ["id"], ondelete="SET NULL",
    )

    # Part B — ODT dispatch consumes real FIFO lots, traceably.
    op.create_table(
        "service_order_transfer_lot_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transfer_line_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(10, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["transfer_line_id"], ["service_order_transfer_lines.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["lot_id"], ["part_lots.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_service_order_transfer_lot_allocations_transfer_line_id",
        "service_order_transfer_lot_allocations", ["transfer_line_id"],
    )

    # Part C — a structured failure cause, and the claim it can trigger.
    failure_category = postgresql.ENUM(
        "REPUESTO_DEFECTUOSO", "MANO_DE_OBRA", "DESGASTE_NORMAL", "OTRO",
        name="rework_failure_category", create_type=False,
    )
    failure_category.create(op.get_bind(), checkfirst=True)
    op.add_column("rework_claims", sa.Column("failure_category", failure_category, nullable=True))
    op.execute("UPDATE rework_claims SET failure_category = 'OTRO' WHERE failure_category IS NULL")
    op.alter_column("rework_claims", "failure_category", nullable=False)

    op.add_column("supplier_claims", sa.Column("lot_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "supplier_claims", sa.Column("purchase_request_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "supplier_claims", sa.Column("rework_claim_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_supplier_claims_lot_id", "supplier_claims", "part_lots", ["lot_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_supplier_claims_purchase_request_id",
        "supplier_claims", "purchase_requests", ["purchase_request_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_supplier_claims_rework_claim_id",
        "supplier_claims", "rework_claims", ["rework_claim_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_supplier_claims_rework_claim_id", "supplier_claims", type_="foreignkey")
    op.drop_constraint("fk_supplier_claims_purchase_request_id", "supplier_claims", type_="foreignkey")
    op.drop_constraint("fk_supplier_claims_lot_id", "supplier_claims", type_="foreignkey")
    op.drop_column("supplier_claims", "rework_claim_id")
    op.drop_column("supplier_claims", "purchase_request_id")
    op.drop_column("supplier_claims", "lot_id")

    op.drop_column("rework_claims", "failure_category")
    op.execute("DROP TYPE IF EXISTS rework_failure_category")

    op.drop_index(
        "ix_service_order_transfer_lot_allocations_transfer_line_id",
        table_name="service_order_transfer_lot_allocations",
    )
    op.drop_table("service_order_transfer_lot_allocations")

    op.drop_constraint("fk_part_lots_purchase_request_id", "part_lots", type_="foreignkey")
    op.drop_column("part_lots", "purchase_request_id")
