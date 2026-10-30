"""Client leads, follow-up metadata and immutable sale/client links."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "c6fd59ab7437"
down_revision = "b5ec48fa6326"
branch_labels = None
depends_on = None


def upgrade():
    for name, length, default in [
        ("lead_origin", 30, "registro_directo"),
        ("lead_interest", 20, "otro"),
        ("lead_status", 25, "nuevo"),
    ]:
        op.add_column(
            "clients", sa.Column(name, sa.String(length), nullable=False, server_default=default)
        )
    op.add_column("clients", sa.Column("lead_vehicle_interest", sa.String(255), nullable=True))
    op.add_column("clients", sa.Column("lead_notes", sa.Text(), nullable=True))
    op.add_column("clients", sa.Column("lead_next_contact_at", sa.Date(), nullable=True))
    for name in ["lead_assigned_user_id", "created_by_user_id"]:
        op.add_column(
            "clients",
            sa.Column(
                name,
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    for table in ["part_sales", "vehicle_sales"]:
        op.add_column(
            table,
            sa.Column(
                "client_id",
                UUID(as_uuid=True),
                sa.ForeignKey("clients.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_client_id", table, ["client_id"])
        op.execute(
            sa.text(
                f"UPDATE {table} s SET client_id=c.id FROM clients c WHERE s.filial_id=c.filial_id AND regexp_replace(s.client_document, '[^0-9]', '', 'g')=c.document_number"
            )
        )


def downgrade():
    for table in ["part_sales", "vehicle_sales"]:
        op.drop_index(f"ix_{table}_client_id", table_name=table)
        op.drop_column(table, "client_id")
    for name in [
        "lead_origin",
        "lead_interest",
        "lead_status",
        "lead_vehicle_interest",
        "lead_notes",
        "lead_next_contact_at",
        "lead_assigned_user_id",
        "created_by_user_id",
    ]:
        op.drop_column("clients", name)
