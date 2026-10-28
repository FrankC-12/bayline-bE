"""Holding recovery OTP challenges and verified recovery phone."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b5ec48fa6326"
down_revision = "a4db37e95215"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("recovery_phone", sa.String(20), nullable=True))
    op.add_column(
        "users", sa.Column("recovery_phone_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "auth_recovery_challenges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_recovery_challenges_user_id", "auth_recovery_challenges", ["user_id"])
    op.create_index(
        "ix_auth_recovery_challenges_expires_at", "auth_recovery_challenges", ["expires_at"]
    )


def downgrade():
    op.drop_table("auth_recovery_challenges")
    op.drop_column("users", "recovery_phone_verified_at")
    op.drop_column("users", "recovery_phone")
