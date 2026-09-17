"""unify ReworkClaim (garantía taller) and WarrantyClaim (garantía fábrica)
into a single "reclamo de garantía" — adds claim_type, a real
solicitado/autorizado/rechazado/convertido_a_ods status, claim numbering,
photos/documents, and migrates existing rework_claims rows across before
dropping that table.

Revision ID: 2f7b4e1a9c56
Revises: 9b2e5f1a7c3d
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "2f7b4e1a9c56"
down_revision: str | Sequence[str] | None = "9b2e5f1a7c3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    claim_type_enum = postgresql.ENUM(
        "FABRICA", "COMEBACK", "REPUESTO_PROVEEDOR", "CAMPANA_RECALL",
        name="warranty_claim_type", create_type=False,
    )
    claim_type_enum.create(op.get_bind(), checkfirst=True)

    # warranty_claim_status only had SOLICITADO — swap in a type with the
    # full set of labels rather than ALTER TYPE ... ADD VALUE, since the new
    # labels are used later in this same migration (backfill below) and
    # Postgres won't allow a freshly added enum value to be used before its
    # ADD VALUE statement is committed in a separate transaction.
    op.execute(
        "CREATE TYPE warranty_claim_status_new AS ENUM "
        "('SOLICITADO', 'AUTORIZADO', 'RECHAZADO', 'CONVERTIDO_A_ODS')"
    )
    op.execute("ALTER TABLE warranty_claims ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE warranty_claims ALTER COLUMN status TYPE warranty_claim_status_new "
        "USING status::text::warranty_claim_status_new"
    )
    op.execute("ALTER TABLE warranty_claims ALTER COLUMN status SET DEFAULT 'SOLICITADO'")
    op.execute("DROP TYPE warranty_claim_status")
    op.execute("ALTER TYPE warranty_claim_status_new RENAME TO warranty_claim_status")

    # New columns on warranty_claims.
    op.add_column("warranty_claims", sa.Column("sequence_number", sa.Integer(), nullable=True))
    op.add_column("warranty_claims", sa.Column("claim_type", claim_type_enum, nullable=True))
    op.add_column(
        "warranty_claims", sa.Column("service_order_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("warranty_claims", sa.Column("tempario_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("warranty_claims", sa.Column("part_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "warranty_claims",
        sa.Column(
            "failure_category",
            postgresql.ENUM(
                "MANO_DE_OBRA", "REPUESTO_DEFECTUOSO", "ERROR_DIAGNOSTICO", "MAL_USO_CLIENTE", "NO_DETERMINADA",
                name="rework_failure_category", create_type=False,
            ),
            nullable=True,
        ),
    )
    op.add_column("warranty_claims", sa.Column("failure_cause", sa.String(300), nullable=True))
    op.add_column("warranty_claims", sa.Column("claimed_at", sa.Date(), nullable=True))
    op.add_column("warranty_claims", sa.Column("note", sa.Text(), nullable=True))
    op.add_column(
        "warranty_claims", sa.Column("photo_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.add_column(
        "warranty_claims", sa.Column("document_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
    )
    op.add_column(
        "warranty_claims", sa.Column("authorized_by_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("warranty_claims", sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "warranty_claims", sa.Column("warranty_override", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("warranty_claims", sa.Column("warranty_override_note", sa.String(500), nullable=True))
    op.add_column(
        "warranty_claims", sa.Column("converted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("warranty_claims", sa.Column("converted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "warranty_claims", sa.Column("resulting_service_order_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.alter_column("warranty_claims", "reported_symptom", nullable=True)

    op.create_foreign_key(
        "fk_warranty_claims_service_order_id", "warranty_claims", "service_orders",
        ["service_order_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_warranty_claims_tempario_id", "warranty_claims", "temparios",
        ["tempario_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_warranty_claims_part_id", "warranty_claims", "parts", ["part_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_warranty_claims_authorized_by_user_id", "warranty_claims", "users",
        ["authorized_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_warranty_claims_converted_by_user_id", "warranty_claims", "users",
        ["converted_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_warranty_claims_resulting_service_order_id", "warranty_claims", "service_orders",
        ["resulting_service_order_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_warranty_claims_service_order_id", "warranty_claims", ["service_order_id"])

    connection = op.get_bind()

    # Backfill existing warranty_claims (all factory claims, all still
    # solicitado today — the feature had no authorization step yet).
    connection.execute(sa.text("""
        UPDATE warranty_claims
        SET claim_type = 'FABRICA', claimed_at = created_at::date
        WHERE claim_type IS NULL
    """))
    connection.execute(sa.text("""
        UPDATE warranty_claims AS w
        SET sequence_number = numbered.rn + 100
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY filial_id ORDER BY created_at) AS rn
            FROM warranty_claims
        ) AS numbered
        WHERE w.id = numbered.id
    """))

    # Migrate rework_claims rows across, preserving id (supplier_claims
    # references it) and continuing the same per-filial sequence.
    connection.execute(sa.text("""
        INSERT INTO warranty_claims (
            id, filial_id, sequence_number, claim_type, vehicle_id, service_order_id,
            tempario_id, part_id, failure_category, failure_cause, reported_mileage,
            claimed_at, recorded_by_user_id, note, photo_urls, document_urls, created_at,
            status, authorized_by_user_id, authorized_at, warranty_override,
            warranty_override_note, resulting_service_order_id
        )
        SELECT
            rc.id, rc.filial_id,
            COALESCE(
                (SELECT MAX(w2.sequence_number) FROM warranty_claims w2 WHERE w2.filial_id = rc.filial_id), 100
            ) + ROW_NUMBER() OVER (PARTITION BY rc.filial_id ORDER BY rc.created_at),
            (CASE WHEN rc.failure_category = 'REPUESTO_DEFECTUOSO' THEN 'REPUESTO_PROVEEDOR' ELSE 'COMEBACK' END)
                ::warranty_claim_type,
            so.vehicle_id, rc.service_order_id, rc.tempario_id, rc.part_id, rc.failure_category,
            rc.failure_cause, COALESCE(so.intake_mileage, 0), rc.claimed_at, rc.recorded_by_user_id,
            rc.note, '[]'::json, '[]'::json, rc.created_at,
            (CASE
                WHEN rc.authorization_status = 'RECHAZADO' THEN 'RECHAZADO'
                WHEN rc.authorization_status = 'APROBADO' AND rc.resulting_service_order_id IS NOT NULL
                    THEN 'CONVERTIDO_A_ODS'
                WHEN rc.authorization_status = 'APROBADO' THEN 'AUTORIZADO'
                ELSE 'SOLICITADO'
            END)::warranty_claim_status,
            rc.authorized_by_user_id, rc.authorized_at, rc.warranty_override, rc.warranty_override_note,
            rc.resulting_service_order_id
        FROM rework_claims AS rc
        JOIN service_orders AS so ON so.id = rc.service_order_id
    """))

    op.alter_column("warranty_claims", "sequence_number", nullable=False)
    op.alter_column("warranty_claims", "claim_type", nullable=False)
    op.alter_column("warranty_claims", "claimed_at", nullable=False)
    op.alter_column("warranty_claims", "photo_urls", server_default=None)
    op.alter_column("warranty_claims", "document_urls", server_default=None)

    # Repoint SupplierClaim -> WarrantyClaim (ids preserved above).
    op.drop_constraint("fk_supplier_claims_rework_claim_id", "supplier_claims", type_="foreignkey")
    op.alter_column("supplier_claims", "rework_claim_id", new_column_name="warranty_claim_id")
    op.create_foreign_key(
        "fk_supplier_claims_warranty_claim_id", "supplier_claims", "warranty_claims",
        ["warranty_claim_id"], ["id"], ondelete="SET NULL",
    )

    op.drop_table("warranty_claim_warranties")
    op.drop_table("rework_claims")
    op.execute("DROP TYPE IF EXISTS rework_authorization_status")
    op.execute("DROP TYPE IF EXISTS rework_claim_status")


def downgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("ABIERTO", "CERRADO", name="rework_claim_status", create_type=False).create(
        bind, checkfirst=True
    )
    postgresql.ENUM(
        "PENDIENTE", "APROBADO", "RECHAZADO", name="rework_authorization_status", create_type=False
    ).create(bind, checkfirst=True)

    op.create_table(
        "rework_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filial_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("service_order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tempario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("part_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "failure_category",
            postgresql.ENUM(
                "MANO_DE_OBRA", "REPUESTO_DEFECTUOSO", "ERROR_DIAGNOSTICO", "MAL_USO_CLIENTE", "NO_DETERMINADA",
                name="rework_failure_category", create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("failure_cause", sa.String(300), nullable=False),
        sa.Column("claimed_at", sa.Date(), nullable=False),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "status", postgresql.ENUM("ABIERTO", "CERRADO", name="rework_claim_status", create_type=False),
            nullable=False, server_default="CERRADO",
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "authorization_status",
            postgresql.ENUM("PENDIENTE", "APROBADO", "RECHAZADO", name="rework_authorization_status", create_type=False),
            nullable=False, server_default="PENDIENTE",
        ),
        sa.Column("authorized_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warranty_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("warranty_override_note", sa.String(500), nullable=True),
        sa.Column("resulting_service_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["filial_id"], ["filiales.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_order_id"], ["service_orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tempario_id"], ["temparios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["part_id"], ["parts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["closed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["authorized_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resulting_service_order_id"], ["service_orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "warranty_claim_warranties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("warranty_claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_warranty_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["warranty_claim_id"], ["warranty_claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vehicle_warranty_id"], ["vehicle_warranties.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("warranty_claim_id", "vehicle_warranty_id", name="uq_warranty_claim_warranty"),
    )

    connection = op.get_bind()
    connection.execute(sa.text("""
        INSERT INTO rework_claims (
            id, filial_id, service_order_id, tempario_id, part_id, failure_category, failure_cause,
            claimed_at, recorded_by_user_id, note, created_at, status, closed_at, closed_by_user_id,
            authorization_status, authorized_by_user_id, authorized_at, warranty_override,
            warranty_override_note, resulting_service_order_id
        )
        SELECT
            id, filial_id, service_order_id, tempario_id, part_id, failure_category,
            COALESCE(failure_cause, ''), claimed_at, recorded_by_user_id, note, created_at,
            (CASE WHEN status = 'SOLICITADO' THEN 'ABIERTO' ELSE 'CERRADO' END)::rework_claim_status,
            CASE WHEN status != 'SOLICITADO' THEN authorized_at ELSE NULL END, authorized_by_user_id,
            (CASE
                WHEN status = 'RECHAZADO' THEN 'RECHAZADO'
                WHEN status IN ('AUTORIZADO', 'CONVERTIDO_A_ODS') THEN 'APROBADO'
                ELSE 'PENDIENTE'
            END)::rework_authorization_status,
            authorized_by_user_id, authorized_at, warranty_override, warranty_override_note,
            resulting_service_order_id
        FROM warranty_claims
        WHERE claim_type IN ('COMEBACK', 'REPUESTO_PROVEEDOR') AND service_order_id IS NOT NULL
    """))

    op.drop_constraint("fk_supplier_claims_warranty_claim_id", "supplier_claims", type_="foreignkey")
    op.alter_column("supplier_claims", "warranty_claim_id", new_column_name="rework_claim_id")
    op.create_foreign_key(
        "fk_supplier_claims_rework_claim_id", "supplier_claims", "rework_claims",
        ["rework_claim_id"], ["id"], ondelete="SET NULL",
    )

    connection.execute(sa.text("""
        DELETE FROM warranty_claims
        WHERE claim_type IN ('COMEBACK', 'REPUESTO_PROVEEDOR') AND service_order_id IS NOT NULL
    """))

    op.drop_index("ix_warranty_claims_service_order_id", table_name="warranty_claims")
    op.drop_constraint("fk_warranty_claims_resulting_service_order_id", "warranty_claims", type_="foreignkey")
    op.drop_constraint("fk_warranty_claims_converted_by_user_id", "warranty_claims", type_="foreignkey")
    op.drop_constraint("fk_warranty_claims_authorized_by_user_id", "warranty_claims", type_="foreignkey")
    op.drop_constraint("fk_warranty_claims_part_id", "warranty_claims", type_="foreignkey")
    op.drop_constraint("fk_warranty_claims_tempario_id", "warranty_claims", type_="foreignkey")
    op.drop_constraint("fk_warranty_claims_service_order_id", "warranty_claims", type_="foreignkey")

    op.alter_column("warranty_claims", "reported_symptom", nullable=False)
    for column in (
        "resulting_service_order_id", "converted_at", "converted_by_user_id", "warranty_override_note",
        "warranty_override", "authorized_at", "authorized_by_user_id", "document_urls", "photo_urls",
        "note", "claimed_at", "failure_cause", "failure_category", "part_id", "tempario_id",
        "service_order_id", "claim_type", "sequence_number",
    ):
        op.drop_column("warranty_claims", column)

    op.execute(
        "CREATE TYPE warranty_claim_status_old AS ENUM ('SOLICITADO')"
    )
    op.execute("ALTER TABLE warranty_claims ALTER COLUMN status DROP DEFAULT")
    op.execute("UPDATE warranty_claims SET status = 'SOLICITADO' WHERE status != 'SOLICITADO'")
    op.execute(
        "ALTER TABLE warranty_claims ALTER COLUMN status TYPE warranty_claim_status_old "
        "USING status::text::warranty_claim_status_old"
    )
    op.execute("ALTER TABLE warranty_claims ALTER COLUMN status SET DEFAULT 'SOLICITADO'")
    op.execute("DROP TYPE warranty_claim_status")
    op.execute("ALTER TYPE warranty_claim_status_old RENAME TO warranty_claim_status")
    op.execute("DROP TYPE IF EXISTS warranty_claim_type")
