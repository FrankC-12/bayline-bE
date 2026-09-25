"""warranty policies (post ventas)

Revision ID: b4d9e2f1a7c3
Revises: a1b2c3d4e5f6
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b4d9e2f1a7c3"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warranty_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column(
            "applies_to",
            sa.Enum("MANO_DE_OBRA", "REPUESTOS", "AMBAS", name="warranty_policy_applies_to"),
            nullable=False,
        ),
        sa.Column(
            "covered_by",
            sa.Enum(
                "LA_CASA", "FABRICA_IMPORTADOR", "PROVEEDOR", name="warranty_policy_covered_by"
            ),
            nullable=False,
        ),
        sa.Column(
            "scope",
            sa.Enum("SOLO_PIEZA", "PIEZA_MAS_INSTALACION", name="warranty_policy_scope"),
            nullable=False,
        ),
        sa.Column("no_expiration", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("duration_km", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("ACTIVA", "INACTIVA", name="warranty_policy_status"),
            nullable=False,
            server_default="ACTIVA",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_warranty_policies_filial_id", "warranty_policies", ["filial_id"])

    op.create_table(
        "warranty_policy_temparios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tempario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["warranty_policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tempario_id"], ["temparios.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "tempario_id", name="uq_warranty_policy_tempario"),
    )
    op.create_index(
        "ix_warranty_policy_temparios_policy_id", "warranty_policy_temparios", ["policy_id"]
    )

    op.create_table(
        "warranty_policy_parts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("part_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["policy_id"], ["warranty_policies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_id", "part_id", name="uq_warranty_policy_part"),
    )
    op.create_index("ix_warranty_policy_parts_policy_id", "warranty_policy_parts", ["policy_id"])

    op.add_column(
        "service_orders", sa.Column("labor_warranty_policy_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "service_orders", sa.Column("parts_warranty_policy_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_service_orders_labor_warranty_policy_id", "service_orders", "warranty_policies",
        ["labor_warranty_policy_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_service_orders_parts_warranty_policy_id", "service_orders", "warranty_policies",
        ["parts_warranty_policy_id"], ["id"], ondelete="SET NULL",
    )

    op.add_column(
        "workshop_warranties", sa.Column("warranty_policy_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "workshop_warranties", sa.Column("warranty_policy_name_snapshot", sa.String(150), nullable=True)
    )
    op.add_column(
        "workshop_warranties",
        sa.Column(
            "covered_by_snapshot",
            sa.Enum(
                "LA_CASA", "FABRICA_IMPORTADOR", "PROVEEDOR", name="warranty_policy_covered_by",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_workshop_warranties_warranty_policy_id", "workshop_warranties", "warranty_policies",
        ["warranty_policy_id"], ["id"], ondelete="SET NULL",
    )
    # Nullable now — a "sin vencimiento" policy has no date/km cutoff at all.
    op.alter_column("workshop_warranties", "expires_at", nullable=True)
    op.alter_column("workshop_warranties", "duration_days", nullable=True)
    op.alter_column("workshop_warranties", "duration_km", nullable=True)


def downgrade() -> None:
    op.alter_column("workshop_warranties", "duration_km", nullable=False)
    op.alter_column("workshop_warranties", "duration_days", nullable=False)
    op.alter_column("workshop_warranties", "expires_at", nullable=False)
    op.drop_constraint("fk_workshop_warranties_warranty_policy_id", "workshop_warranties", type_="foreignkey")
    op.drop_column("workshop_warranties", "covered_by_snapshot")
    op.drop_column("workshop_warranties", "warranty_policy_name_snapshot")
    op.drop_column("workshop_warranties", "warranty_policy_id")

    op.drop_constraint("fk_service_orders_parts_warranty_policy_id", "service_orders", type_="foreignkey")
    op.drop_constraint("fk_service_orders_labor_warranty_policy_id", "service_orders", type_="foreignkey")
    op.drop_column("service_orders", "parts_warranty_policy_id")
    op.drop_column("service_orders", "labor_warranty_policy_id")

    op.drop_index("ix_warranty_policy_parts_policy_id", table_name="warranty_policy_parts")
    op.drop_table("warranty_policy_parts")
    op.drop_index("ix_warranty_policy_temparios_policy_id", table_name="warranty_policy_temparios")
    op.drop_table("warranty_policy_temparios")
    op.drop_index("ix_warranty_policies_filial_id", table_name="warranty_policies")
    op.drop_table("warranty_policies")

    op.execute("DROP TYPE IF EXISTS warranty_policy_status")
    op.execute("DROP TYPE IF EXISTS warranty_policy_scope")
    op.execute("DROP TYPE IF EXISTS warranty_policy_covered_by")
    op.execute("DROP TYPE IF EXISTS warranty_policy_applies_to")
