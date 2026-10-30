"""Configurable dealership rules and frozen quote terms."""

from alembic import op
import sqlalchemy as sa

revision = "e8bf71cd9659"
down_revision = "d7ae60bc8548"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "labor_settings",
        sa.Column("vehicle_quote_valid_days", sa.Integer(), nullable=False, server_default="15"),
    )
    op.add_column(
        "labor_settings",
        sa.Column("vehicle_reservation_days", sa.Integer(), nullable=False, server_default="7"),
    )
    op.add_column(
        "labor_settings",
        sa.Column(
            "vehicle_min_deposit_percentage", sa.Numeric(5, 2), nullable=False, server_default="10"
        ),
    )
    op.add_column(
        "labor_settings",
        sa.Column(
            "vehicle_discount_without_approval_percentage",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="3",
        ),
    )
    op.add_column(
        "labor_settings",
        sa.Column(
            "vehicle_withdrawal_retention_percentage",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="10",
        ),
    )
    op.add_column("vehicle_opportunities", sa.Column("quote_expires_at", sa.Date(), nullable=True))
    op.add_column(
        "vehicle_opportunities",
        sa.Column("discount_percentage", sa.Numeric(5, 2), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("vehicle_opportunities", "discount_percentage")
    op.drop_column("vehicle_opportunities", "quote_expires_at")
    op.drop_column("labor_settings", "vehicle_withdrawal_retention_percentage")
    op.drop_column("labor_settings", "vehicle_discount_without_approval_percentage")
    op.drop_column("labor_settings", "vehicle_min_deposit_percentage")
    op.drop_column("labor_settings", "vehicle_reservation_days")
    op.drop_column("labor_settings", "vehicle_quote_valid_days")
