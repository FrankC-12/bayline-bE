"""Default tempario guarantees and frozen task terms."""

import sqlalchemy as sa

from alembic import op

revision = "e1b8c9d30f24"
down_revision = "d9a6b7f20e13"
branch_labels = None
depends_on = None


def upgrade():
    for coverage in ("labor", "parts"):
        column = f"{coverage}_warranty_policy_id"
        op.add_column("temparios", sa.Column(column, sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"fk_temparios_{column}",
            "temparios",
            "warranty_policies",
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
    op.add_column("service_order_tasks", sa.Column("warranty_snapshot", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("service_order_tasks", "warranty_snapshot")
    for coverage in ("parts", "labor"):
        column = f"{coverage}_warranty_policy_id"
        op.drop_constraint(f"fk_temparios_{column}", "temparios", type_="foreignkey")
        op.drop_column("temparios", column)
