"""Tipos de ODS — replaces the fixed service_order_type enum with a
filial-scoped, admin-manageable catalog (service_order_types: name,
description, is_system, claim_type, is_selectable, is_active). Seeds the 6
system rows (regular/mpt/retrabajo/garantia_fabrica/comeback/campana) for
every existing filial, backfills service_orders.order_type_id from the old
enum column by matching code, then drops the enum column/type.

Revision ID: a7c3e9f12b4d
Revises: 8f1b6c4d2a7e
Create Date: 2026-10-06
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7c3e9f12b4d"
down_revision: str | Sequence[str] | None = "8f1b6c4d2a7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (code, name, description, claim_type, is_selectable) — mirrors
# app.modules.service_orders.service.SYSTEM_ORDER_TYPES. Migrations are
# self-contained and never import app code, so this is intentionally
# duplicated rather than imported.
SYSTEM_ORDER_TYPES = [
    (
        "regular", "Regular",
        "Mantenimiento o reparación estándar, sin garantía ni reclamo asociado.",
        None, True,
    ),
    (
        "mpt", "MPT",
        "Reservado para un futuro módulo de mantenimiento programado — "
        "no disponible para selección manual todavía.",
        None, False,
    ),
    (
        "retrabajo", "Retrabajo",
        "Se asigna automáticamente al convertir un reclamo de garantía de "
        "taller en una orden — nunca se elige a mano.",
        None, False,
    ),
    (
        "garantia_fabrica", "Garantía de fábrica",
        "Requiere vincular un reclamo de garantía de fábrica autorizado para el mismo vehículo.",
        "FABRICA", True,
    ),
    (
        "comeback", "Comeback",
        "Requiere vincular un reclamo de garantía de taller (comeback) autorizado para el mismo vehículo.",
        "COMEBACK", True,
    ),
    (
        "campana", "Campaña / Recall",
        "Requiere vincular un reclamo de campaña o recall autorizado para el mismo vehículo.",
        "CAMPANA_RECALL", True,
    ),
]


def upgrade() -> None:
    claim_type_enum = postgresql.ENUM(
        "FABRICA", "COMEBACK", "REPUESTO_PROVEEDOR", "CAMPANA_RECALL",
        name="warranty_claim_type", create_type=False,
    )

    op.create_table(
        "service_order_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("claim_type", claim_type_enum, nullable=True),
        sa.Column("is_selectable", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filial_id", "code", name="uq_service_order_types_filial_code"),
    )
    op.create_index("ix_service_order_types_filial_id", "service_order_types", ["filial_id"])

    bind = op.get_bind()
    order_types_table = sa.table(
        "service_order_types",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("filial_id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_system", sa.Boolean),
        sa.column("claim_type", claim_type_enum),
        sa.column("is_selectable", sa.Boolean),
    )
    filial_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM filiales"))]
    rows = [
        {
            "id": uuid.uuid4(), "filial_id": filial_id, "code": code, "name": name,
            "description": description, "is_system": True, "claim_type": claim_type,
            "is_selectable": is_selectable,
        }
        for filial_id in filial_ids
        for code, name, description, claim_type, is_selectable in SYSTEM_ORDER_TYPES
    ]
    if rows:
        op.bulk_insert(order_types_table, rows)

    op.add_column("service_orders", sa.Column("order_type_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        "UPDATE service_orders so SET order_type_id = sot.id "
        "FROM service_order_types sot "
        "WHERE sot.filial_id = so.filial_id AND sot.code = lower(so.order_type::text)"
    )
    op.alter_column("service_orders", "order_type_id", nullable=False)
    op.create_foreign_key(
        "fk_service_orders_order_type_id", "service_orders", "service_order_types",
        ["order_type_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index("ix_service_orders_order_type_id", "service_orders", ["order_type_id"])

    op.drop_column("service_orders", "order_type")
    op.execute("DROP TYPE service_order_type")


def downgrade() -> None:
    service_order_type_enum = postgresql.ENUM(
        "REGULAR", "MPT", "RETRABAJO", "GARANTIA_FABRICA", "COMEBACK", "CAMPANA",
        name="service_order_type",
    )
    service_order_type_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "service_orders",
        sa.Column("order_type", service_order_type_enum, nullable=True),
    )
    op.execute(
        "UPDATE service_orders so SET order_type = upper(sot.code)::service_order_type "
        "FROM service_order_types sot WHERE sot.id = so.order_type_id"
    )
    op.alter_column("service_orders", "order_type", nullable=False)

    op.drop_index("ix_service_orders_order_type_id", table_name="service_orders")
    op.drop_constraint("fk_service_orders_order_type_id", "service_orders", type_="foreignkey")
    op.drop_column("service_orders", "order_type_id")

    op.drop_index("ix_service_order_types_filial_id", table_name="service_order_types")
    op.drop_table("service_order_types")
