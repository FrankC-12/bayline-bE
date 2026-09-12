"""warranty claims — intake screen for factory-warranty comeback complaints,
raised before any ServiceOrder exists; stays 'solicitado' until a manager
authorizes it (future work)

Revision ID: f1a4d7c2e963
Revises: e6c9f3a8b264
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f1a4d7c2e963"
down_revision: str | Sequence[str] | None = "e6c9f3a8b264"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM("SOLICITADO", name="warranty_claim_status", create_type=False)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "warranty_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reported_symptom", sa.Text(), nullable=False),
        sa.Column("reported_mileage", sa.Integer(), nullable=False),
        sa.Column("vehicle_mileage_at_claim", sa.Integer(), nullable=True),
        sa.Column("mileage_inconsistent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", status_enum, nullable=False, server_default="SOLICITADO"),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_warranty_claims_filial_id", "warranty_claims", ["filial_id"])
    op.create_index("ix_warranty_claims_vehicle_id", "warranty_claims", ["vehicle_id"])

    op.create_table(
        "warranty_claim_warranties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("warranty_claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_warranty_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["warranty_claim_id"], ["warranty_claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vehicle_warranty_id"], ["vehicle_warranties.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("warranty_claim_id", "vehicle_warranty_id", name="uq_warranty_claim_warranty"),
    )
    op.create_index(
        "ix_warranty_claim_warranties_warranty_claim_id",
        "warranty_claim_warranties",
        ["warranty_claim_id"],
    )


def downgrade() -> None:
    op.drop_table("warranty_claim_warranties")
    op.drop_table("warranty_claims")
    op.execute("DROP TYPE IF EXISTS warranty_claim_status")
