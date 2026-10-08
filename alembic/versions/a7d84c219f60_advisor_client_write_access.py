"""Allow the standard service advisor role to register clients.

Only promote the legacy read permission. Missing role permissions and
per-user overrides represent explicit restrictions and remain untouched.
"""

import sqlalchemy as sa

from alembic import op

revision = "a7d84c219f60"
down_revision = "f2c9d0e41a35"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        UPDATE role_module_permissions AS permission
        SET access = 'EDITAR'
        FROM roles AS role
        WHERE permission.role_id = role.id
          AND role.slug = 'asesor'
          AND role.scope = 'FILIAL'
          AND permission.module_id = 'clientes-vehiculos'
          AND permission.access = 'VER'
    """))


def downgrade():
    # Data correction: cannot distinguish migrated grants from an administrator's
    # later choice. Do not revoke legitimate access during a schema rollback.
    pass
