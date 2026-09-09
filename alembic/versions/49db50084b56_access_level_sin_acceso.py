"""Add SIN_ACCESO to the access_level enum, for per-user overrides that
revoke access below the role's default.

Revision ID: 49db50084b56
Revises: b512cca98fe5
"""

from alembic import op

revision = "49db50084b56"
down_revision = "b512cca98fe5"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres can't add an enum value inside the transaction block Alembic
    # normally wraps each migration in.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE access_level ADD VALUE 'SIN_ACCESO'")


def downgrade():
    # Postgres has no "remove enum value" operation short of recreating the
    # type; left as a documented no-op rather than a destructive rebuild.
    pass
