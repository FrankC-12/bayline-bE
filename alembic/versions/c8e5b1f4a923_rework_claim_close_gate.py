"""rework claim open/closed lifecycle — failure_category required to close,
not at creation

Revision ID: c8e5b1f4a923
Revises: b7f3a9d2c841
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c8e5b1f4a923"
down_revision: str | Sequence[str] | None = "b7f3a9d2c841"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM("ABIERTO", "CERRADO", name="rework_claim_status", create_type=False)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "rework_claims",
        sa.Column("status", status_enum, nullable=False, server_default="ABIERTO"),
    )
    op.add_column("rework_claims", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "rework_claims", sa.Column("closed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_rework_claims_closed_by_user_id", "rework_claims", "users", ["closed_by_user_id"], ["id"],
        ondelete="SET NULL",
    )

    # Every existing claim was created under the old all-at-once workflow
    # (category was mandatory then) — treat them as already closed, and
    # remap the two retired categories to the new catch-all.
    op.execute(
        "UPDATE rework_claims SET failure_category = 'NO_DETERMINADA' "
        "WHERE failure_category IN ('DESGASTE_NORMAL', 'OTRO')"
    )
    op.execute("UPDATE rework_claims SET status = 'CERRADO', closed_at = created_at")

    op.alter_column("rework_claims", "failure_category", nullable=True)


def downgrade() -> None:
    op.alter_column("rework_claims", "failure_category", nullable=False)

    op.drop_constraint("fk_rework_claims_closed_by_user_id", "rework_claims", type_="foreignkey")
    op.drop_column("rework_claims", "closed_by_user_id")
    op.drop_column("rework_claims", "closed_at")
    op.drop_column("rework_claims", "status")
    op.execute("DROP TYPE IF EXISTS rework_claim_status")
