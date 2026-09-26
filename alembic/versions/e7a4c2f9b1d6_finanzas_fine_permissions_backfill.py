"""finanzas fine-grained permissions backfill (cobrar/egreso/reversar/rentabilidad)

Revision ID: e7a4c2f9b1d6
Revises: d5e8b3a1c9f2
Create Date: 2026-09-28
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7a4c2f9b1d6"
down_revision: str | Sequence[str] | None = "d5e8b3a1c9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (source module a role already had, new fine-grained module it gets the
# same access level on) — same backfill idiom as f2c8a4d0e6b1's
# administracion -> compras split, so no existing role loses an ability it
# already had the day this ships; only future role/permission edits need to
# manage the four new permissions independently.
BACKFILL_PAIRS = [
    ("administracion", "finanzas-rentabilidad"),
    ("movimientos-manuales", "finanzas-egreso"),
    ("movimientos-manuales", "finanzas-reversar"),
    ("asesor-servicios", "finanzas-cobrar"),
]


def upgrade() -> None:
    connection = op.get_bind()
    insert = sa.text(
        "INSERT INTO role_module_permissions (id, role_id, module_id, access) "
        "VALUES (:id, :role_id, :module_id, :access) "
        "ON CONFLICT ON CONSTRAINT uq_role_module DO NOTHING"
    )
    for source_module, new_module in BACKFILL_PAIRS:
        rows = connection.execute(
            sa.text("SELECT role_id, access FROM role_module_permissions WHERE module_id = :module_id"),
            {"module_id": source_module},
        ).fetchall()
        for role_id, access in rows:
            connection.execute(
                insert,
                {"id": str(uuid.uuid4()), "role_id": str(role_id), "module_id": new_module, "access": access},
            )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_module_permissions WHERE module_id IN "
        "('finanzas-cobrar', 'finanzas-egreso', 'finanzas-reversar', 'finanzas-rentabilidad')"
    )
