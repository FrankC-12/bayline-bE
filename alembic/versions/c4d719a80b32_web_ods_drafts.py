"""Web ODS drafts and warranty-led claim review."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "c4d719a80b32"
down_revision = "b7e29c6140fa"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("service_orders", "vehicle_id", nullable=True)
    for column in (
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("draft_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("creation_source", sa.String(12), nullable=False, server_default="mobile"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_upsells", sa.JSON(), nullable=False, server_default="[]"),
    ):
        op.add_column("service_orders", column)
    op.add_column(
        "upsells",
        sa.Column(
            "resolved_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    for column in (
        sa.Column(
            "brand_responded_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("coverage_snapshot", sa.JSON(), nullable=True),
        sa.Column("workshop_warranty_id", UUID(as_uuid=True), nullable=True),
        sa.Column("factory_warranty_id", UUID(as_uuid=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("brand_status", sa.String(20), nullable=True),
        sa.Column("brand_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("brand_response_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("warranty_claims", column)


def downgrade():
    # Never silently destroy drafts which have no vehicle yet.
    pending = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM service_orders WHERE vehicle_id IS NULL"))
        .scalar()
    )
    if pending:
        raise RuntimeError(
            "Descarta o completa los borradores sin vehículo antes de revertir esta migración."
        )
    op.drop_column("upsells", "resolved_by_user_id")
    for name in (
        "brand_responded_by_user_id",
        "brand_response_at",
        "brand_sent_at",
        "brand_status",
        "rejection_reason",
        "factory_warranty_id",
        "workshop_warranty_id",
        "coverage_snapshot",
    ):
        op.drop_column("warranty_claims", name)
    for name in ("draft_upsells", "submitted_at", "creation_source", "draft_revision", "is_draft"):
        op.drop_column("service_orders", name)
    op.alter_column("service_orders", "vehicle_id", nullable=False)
