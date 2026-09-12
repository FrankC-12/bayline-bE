"""part counter sale warranties

Revision ID: c230c8f08717
Revises: 035129fd211c
Create Date: 2026-09-09 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c230c8f08717"
down_revision = "035129fd211c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "labor_settings",
        sa.Column("part_warranty_days", sa.Integer(), nullable=False, server_default="90"),
    )

    op.create_table(
        "part_warranties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "filial_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("filiales.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "part_sale_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("part_sale_lines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "part_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("parts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "lot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("part_lots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("warranty_days", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_part_warranties_filial_id", "part_warranties", ["filial_id"])
    op.create_index("ix_part_warranties_part_sale_line_id", "part_warranties", ["part_sale_line_id"])


def downgrade():
    op.drop_index("ix_part_warranties_part_sale_line_id", table_name="part_warranties")
    op.drop_index("ix_part_warranties_filial_id", table_name="part_warranties")
    op.drop_table("part_warranties")
    op.drop_column("labor_settings", "part_warranty_days")
