"""Pre-sale financing evidence, PDI and delivery workflow."""

from alembic import op
import sqlalchemy as sa

revision = "f9c082de076a"
down_revision = "e8bf71cd9659"
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE service_order_payer ADD VALUE IF NOT EXISTS 'INTERNO'")
        op.execute("ALTER TYPE movement_source_type ADD VALUE IF NOT EXISTS 'VEHICLE_RESERVATION'")
    op.add_column(
        "vehicle_opportunities",
        sa.Column("workflow_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "vehicle_opportunities",
        sa.Column("workflow", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "vehicle_opportunities",
        sa.Column(
            "pdi_order_id",
            sa.UUID(),
            sa.ForeignKey("service_orders.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_vehicle_opportunity_pdi", "vehicle_opportunities", ["pdi_order_id"]
    )
    op.create_table(
        "vehicle_reservation_settlements",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.UUID(),
            sa.ForeignKey("vehicle_opportunities.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "filial_id",
            sa.UUID(),
            sa.ForeignKey("filiales.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "deposit_income_id",
            sa.UUID(),
            sa.ForeignKey("income_entries.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "refund_expense_id",
            sa.UUID(),
            sa.ForeignKey("expense_entries.id", ondelete="RESTRICT"),
            nullable=True,
            unique=True,
        ),
        sa.Column("retained_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("refund_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("retention_percentage", sa.Numeric(5, 2), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("vehicle_reservation_settlements")
    op.drop_constraint("uq_vehicle_opportunity_pdi", "vehicle_opportunities", type_="unique")
    op.drop_column("vehicle_opportunities", "pdi_order_id")
    op.drop_column("vehicle_opportunities", "workflow")
    op.drop_column("vehicle_opportunities", "workflow_version")
    # PostgreSQL enum values remain to preserve financial/history compatibility.
