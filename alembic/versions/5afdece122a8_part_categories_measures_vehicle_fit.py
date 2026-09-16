"""part categories/measures catalog + structured vehicle fit on parts

Revision ID: 5afdece122a8
Revises: 31254278989d
Create Date: 2026-09-15
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "5afdece122a8"
down_revision: str | Sequence[str] | None = "31254278989d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "part_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("holding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["holding_id"], ["holdings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_part_categories_holding_id", "part_categories", ["holding_id"])

    op.create_table(
        "part_measures",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("holding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["holding_id"], ["holdings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_part_measures_holding_id", "part_measures", ["holding_id"])

    op.add_column("parts", sa.Column("manufacturer_part_number", sa.String(80), nullable=True))
    op.add_column("parts", sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("parts", sa.Column("vehicle_brand_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("parts", sa.Column("vehicle_model_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("parts", sa.Column("year_from", sa.Integer(), nullable=True))
    op.add_column("parts", sa.Column("year_to", sa.Integer(), nullable=True))
    op.add_column("parts", sa.Column("measure_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "parts", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
    )

    connection = op.get_bind()

    # Backfill: one PartCategory per distinct (holding, category-string) among
    # existing parts, preserving whatever free-text value was there as a real
    # catalog entry an admin can rename/deactivate later from Ajustes.
    category_rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT p.category AS category_name, f.holding_id AS holding_id
            FROM parts p
            JOIN filiales f ON f.id = p.filial_id
            """
        )
    ).fetchall()
    category_id_by_key: dict[tuple[str, str], str] = {}
    for row in category_rows:
        category_id = str(uuid.uuid4())
        connection.execute(
            sa.text(
                "INSERT INTO part_categories (id, holding_id, name, is_active) "
                "VALUES (:id, :holding_id, :name, true)"
            ),
            {"id": category_id, "holding_id": str(row.holding_id), "name": row.category_name},
        )
        category_id_by_key[(str(row.holding_id), row.category_name)] = category_id

    # Old `brand` was free text (often "Sin marca") — only carry it forward as
    # vehicle_brand_id where it happens to match an existing catalog brand by
    # name; otherwise leave null (a generic/universal part), never guess.
    part_rows = connection.execute(
        sa.text(
            """
            SELECT p.id AS id, p.category AS category_name, p.brand AS brand_name,
                   f.holding_id AS holding_id
            FROM parts p
            JOIN filiales f ON f.id = p.filial_id
            """
        )
    ).fetchall()
    for row in part_rows:
        category_id = category_id_by_key[(str(row.holding_id), row.category_name)]
        vehicle_brand_id = connection.execute(
            sa.text(
                "SELECT id FROM vehicle_brands WHERE holding_id = :holding_id AND lower(name) = lower(:name)"
            ),
            {"holding_id": str(row.holding_id), "name": row.brand_name},
        ).scalar()
        connection.execute(
            sa.text(
                "UPDATE parts SET category_id = :category_id, vehicle_brand_id = :vehicle_brand_id "
                "WHERE id = :id"
            ),
            {"category_id": category_id, "vehicle_brand_id": vehicle_brand_id, "id": str(row.id)},
        )

    op.alter_column("parts", "category_id", nullable=False)

    op.create_foreign_key(
        "fk_parts_category_id", "parts", "part_categories", ["category_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_foreign_key(
        "fk_parts_vehicle_brand_id", "parts", "vehicle_brands", ["vehicle_brand_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_parts_vehicle_model_id", "parts", "vehicle_models", ["vehicle_model_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_parts_measure_id", "parts", "part_measures", ["measure_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_parts_category_id", "parts", ["category_id"])
    op.create_index("ix_parts_vehicle_brand_id", "parts", ["vehicle_brand_id"])
    op.create_index("ix_parts_vehicle_model_id", "parts", ["vehicle_model_id"])
    op.create_index("ix_parts_measure_id", "parts", ["measure_id"])

    op.drop_column("parts", "category")
    op.drop_column("parts", "brand")
    op.drop_column("parts", "application")


def downgrade() -> None:
    op.add_column("parts", sa.Column("category", sa.String(80), nullable=False, server_default="Sin categoría"))
    op.add_column("parts", sa.Column("brand", sa.String(80), nullable=False, server_default="Sin marca"))
    op.add_column("parts", sa.Column("application", sa.String(180), nullable=False, server_default="Universal"))

    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            UPDATE parts p
            SET category = pc.name
            FROM part_categories pc
            WHERE pc.id = p.category_id
            """
        )
    )
    connection.execute(
        sa.text(
            """
            UPDATE parts p
            SET brand = vb.name
            FROM vehicle_brands vb
            WHERE vb.id = p.vehicle_brand_id
            """
        )
    )

    op.drop_constraint("fk_parts_measure_id", "parts", type_="foreignkey")
    op.drop_constraint("fk_parts_vehicle_model_id", "parts", type_="foreignkey")
    op.drop_constraint("fk_parts_vehicle_brand_id", "parts", type_="foreignkey")
    op.drop_constraint("fk_parts_category_id", "parts", type_="foreignkey")
    op.drop_index("ix_parts_measure_id", table_name="parts")
    op.drop_index("ix_parts_vehicle_model_id", table_name="parts")
    op.drop_index("ix_parts_vehicle_brand_id", table_name="parts")
    op.drop_index("ix_parts_category_id", table_name="parts")

    op.drop_column("parts", "is_active")
    op.drop_column("parts", "measure_id")
    op.drop_column("parts", "year_to")
    op.drop_column("parts", "year_from")
    op.drop_column("parts", "vehicle_model_id")
    op.drop_column("parts", "vehicle_brand_id")
    op.drop_column("parts", "category_id")
    op.drop_column("parts", "manufacturer_part_number")

    op.drop_index("ix_part_measures_holding_id", table_name="part_measures")
    op.drop_table("part_measures")
    op.drop_index("ix_part_categories_holding_id", table_name="part_categories")
    op.drop_table("part_categories")
