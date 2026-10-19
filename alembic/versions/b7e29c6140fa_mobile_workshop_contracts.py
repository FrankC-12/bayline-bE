"""Mobile workshop evidence, refresh sessions, device registration and push outbox."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b7e29c6140fa"
down_revision = "e2a7619c4b83"
branch_labels = None
depends_on = None
U = postgresql.UUID(as_uuid=True)


def upgrade():
    op.add_column(
        "service_orders",
        sa.Column("vehicle_received_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("service_orders", sa.Column("vehicle_received_by_user_id", U, nullable=True))
    for column in (
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("photo_urls", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("pause_reason", sa.String(40), nullable=True),
        sa.Column("pause_history", sa.JSON(), nullable=False, server_default="[]"),
    ):
        op.add_column("service_order_tasks", column)
    op.add_column(
        "service_order_transfers", sa.Column("receipt_incident", sa.JSON(), nullable=True)
    )
    op.add_column(
        "service_order_transfers",
        sa.Column("receipt_incident_history", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "preliminary_inspections", sa.Column("mileage_correction_reason", sa.Text(), nullable=True)
    )
    op.create_table(
        "auth_refresh_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", U, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_refresh_sessions_user_id", "auth_refresh_sessions", ["user_id"])
    op.create_table(
        "auth_login_buckets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_auth_login_buckets_expires_at", "auth_login_buckets", ["expires_at"])
    op.create_table(
        "notification_devices",
        sa.Column("id", U, primary_key=True),
        sa.Column("user_id", U, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token", sa.String(4096), nullable=False),
        sa.Column("platform", sa.String(10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_notification_devices_user_id", "notification_devices", ["user_id"])
    op.create_table(
        "notifications",
        sa.Column("id", U, primary_key=True),
        sa.Column("user_id", U, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("delivered_devices", sa.JSON(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", "event_key"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index("ix_notifications_next_attempt_at", "notifications", ["next_attempt_at"])
    # Stop legacy simultaneous timers without losing already worked time.
    op.execute("""WITH ranked AS (
        SELECT t.id, row_number() OVER (PARTITION BY o.technician_user_id ORDER BY t.timer_started_at DESC, t.id) AS rn
        FROM service_order_tasks t JOIN service_orders o ON o.id=t.service_order_id
        WHERE t.timer_started_at IS NOT NULL AND o.technician_user_id IS NOT NULL)
        UPDATE service_order_tasks t SET
            timer_accumulated_seconds=t.timer_accumulated_seconds + GREATEST(0, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-t.timer_started_at)))::integer,
            timer_started_at=NULL, pause_reason='otra_tarea'
        FROM ranked r WHERE r.id=t.id AND r.rn>1""")
    op.execute(
        """UPDATE service_order_tasks t SET
        timer_accumulated_seconds=t.timer_accumulated_seconds + GREATEST(0, EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP-t.timer_started_at)))::integer,
        timer_started_at=NULL
        FROM service_orders o WHERE o.id=t.service_order_id AND t.timer_started_at IS NOT NULL
        AND (o.status IN ('ORDEN_CERRADA','CANCELADO') OR t.status IN ('COMPLETADA','CANCELADA'))"""
    )
    # Old completed orders can still have outstanding warehouse work. Keep
    # their state, repair the misleading flag without rewriting history.
    op.execute("""UPDATE service_orders o SET completed_with_pending_items = true
        WHERE o.status = 'COMPLETADO' AND EXISTS (
            SELECT 1 FROM service_order_transfers t JOIN service_order_transfer_lines l ON l.transfer_id=t.id
            WHERE t.service_order_id=o.id AND t.picked_up_at IS NULL)""")


def downgrade():
    for table in (
        "notifications",
        "notification_devices",
        "auth_login_buckets",
        "auth_refresh_sessions",
    ):
        op.drop_table(table)
    op.drop_column("preliminary_inspections", "mileage_correction_reason")
    for name in ("receipt_incident_history", "receipt_incident"):
        op.drop_column("service_order_transfers", name)
    for name in ("pause_history", "pause_reason", "cancellation_reason", "photo_urls", "notes"):
        op.drop_column("service_order_tasks", name)
    op.drop_column("service_orders", "vehicle_received_by_user_id")
    op.drop_column("service_orders", "vehicle_received_at")
