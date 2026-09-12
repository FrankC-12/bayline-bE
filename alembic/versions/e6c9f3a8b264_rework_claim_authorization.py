"""manager authorization for rework claims — approve opens a retrabajo
order, reject opens a regular one; vehicle warranty gate with override

Revision ID: e6c9f3a8b264
Revises: d4a8c2e6f157
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e6c9f3a8b264"
down_revision: str | Sequence[str] | None = "d4a8c2e6f157"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        "PENDIENTE", "APROBADO", "RECHAZADO", name="rework_authorization_status", create_type=False
    )
    status_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "rework_claims",
        sa.Column("authorization_status", status_enum, nullable=False, server_default="PENDIENTE"),
    )
    op.add_column(
        "rework_claims", sa.Column("authorized_by_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("rework_claims", sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "rework_claims",
        sa.Column("warranty_override", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("rework_claims", sa.Column("warranty_override_note", sa.String(500), nullable=True))
    op.add_column(
        "rework_claims",
        sa.Column("resulting_service_order_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.create_foreign_key(
        "fk_rework_claims_authorized_by_user_id", "rework_claims", "users", ["authorized_by_user_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_rework_claims_resulting_service_order_id",
        "rework_claims", "service_orders", ["resulting_service_order_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_rework_claims_resulting_service_order_id", "rework_claims", type_="foreignkey")
    op.drop_constraint("fk_rework_claims_authorized_by_user_id", "rework_claims", type_="foreignkey")

    op.drop_column("rework_claims", "resulting_service_order_id")
    op.drop_column("rework_claims", "warranty_override_note")
    op.drop_column("rework_claims", "warranty_override")
    op.drop_column("rework_claims", "authorized_at")
    op.drop_column("rework_claims", "authorized_by_user_id")
    op.drop_column("rework_claims", "authorization_status")
    op.execute("DROP TYPE IF EXISTS rework_authorization_status")
