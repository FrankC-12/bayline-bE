"""Holding registers and transactional tenant audit."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "c0e671af4201"
down_revision = "b9c264fd5087"
branch_labels = None
depends_on = None


def upgrade():
    uid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "holding_registers",
        sa.Column("id", uid, primary_key=True),
        sa.Column(
            "holding_id", uid, sa.ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "filial_id", uid, sa.ForeignKey("filiales.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("request_id", uid, nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_by", uid, sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("holding_id", "request_id", name="uq_holding_register_request"),
    )
    op.create_index(
        "ix_holding_register_scope", "holding_registers", ["holding_id", "kind", "entry_date"]
    )
    op.create_table(
        "holding_audit_events",
        sa.Column("id", uid, primary_key=True),
        sa.Column(
            "holding_id", uid, sa.ForeignKey("holdings.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "filial_id", uid, sa.ForeignKey("filiales.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("entity", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("changed_fields", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_holding_audit_chronology", "holding_audit_events", ["holding_id", "created_at"]
    )


def downgrade():
    op.drop_table("holding_audit_events")
    op.drop_table("holding_registers")
