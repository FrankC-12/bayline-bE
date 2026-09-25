"""compras module permissions backfill

Revision ID: f2c8a4d0e6b1
Revises: e51a7c93b048
Create Date: 2026-09-25
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2c8a4d0e6b1"
down_revision: str | Sequence[str] | None = "e51a7c93b048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Proveedores/Compras a Proveedores/Reclamos a Proveedor moved out of the
    # "administracion" module into a new "compras" module — any role that
    # already had "administracion" access keeps the same access level on
    # "compras", so existing users don't lose access to screens they could
    # already use.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT role_id, access FROM role_module_permissions WHERE module_id = 'administracion'")
    ).fetchall()
    insert = sa.text(
        "INSERT INTO role_module_permissions (id, role_id, module_id, access) "
        "VALUES (:id, :role_id, 'compras', :access) "
        "ON CONFLICT ON CONSTRAINT uq_role_module DO NOTHING"
    )
    for role_id, access in rows:
        connection.execute(insert, {"id": str(uuid.uuid4()), "role_id": str(role_id), "access": access})


def downgrade() -> None:
    op.execute("DELETE FROM role_module_permissions WHERE module_id = 'compras'")
