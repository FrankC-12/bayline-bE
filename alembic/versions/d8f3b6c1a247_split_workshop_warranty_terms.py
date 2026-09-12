"""split the workshop warranty into two terms (mano de obra vs repuesto) —
a bad install fails fast, a part wears out slowly; also link transfer lines
back to the task that caused them, so billing can tell which tasks
installed a part

Revision ID: d8f3b6c1a247
Revises: c4e7a3f9d152
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d8f3b6c1a247"
down_revision: str | Sequence[str] | None = "c4e7a3f9d152"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "labor_settings",
        sa.Column("workshop_parts_warranty_days", sa.Integer(), nullable=False, server_default="90"),
    )
    op.add_column(
        "labor_settings",
        sa.Column("workshop_parts_warranty_km", sa.Integer(), nullable=False, server_default="5000"),
    )

    coverage_enum = postgresql.ENUM(
        "MANO_DE_OBRA", "REPUESTO", name="workshop_warranty_coverage", create_type=False
    )
    coverage_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "workshop_warranties",
        sa.Column("coverage_type", coverage_enum, nullable=False, server_default="MANO_DE_OBRA"),
    )
    op.drop_constraint("uq_workshop_warranty_task", "workshop_warranties", type_="unique")
    op.create_unique_constraint(
        "uq_workshop_warranty_task_coverage",
        "workshop_warranties",
        ["service_order_task_id", "coverage_type"],
    )

    op.add_column(
        "service_order_transfer_lines",
        sa.Column("service_order_task_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_order_transfer_lines_task_id",
        "service_order_transfer_lines",
        "service_order_tasks",
        ["service_order_task_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_service_order_transfer_lines_task_id", "service_order_transfer_lines", type_="foreignkey"
    )
    op.drop_column("service_order_transfer_lines", "service_order_task_id")

    op.drop_constraint("uq_workshop_warranty_task_coverage", "workshop_warranties", type_="unique")
    op.create_unique_constraint(
        "uq_workshop_warranty_task", "workshop_warranties", ["service_order_task_id"]
    )
    op.drop_column("workshop_warranties", "coverage_type")
    op.execute("DROP TYPE IF EXISTS workshop_warranty_coverage")

    op.drop_column("labor_settings", "workshop_parts_warranty_km")
    op.drop_column("labor_settings", "workshop_parts_warranty_days")
