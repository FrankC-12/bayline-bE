"""Track dispatched quantity per part-sale line, to detect a mismatch
against what was sold when the almacenista marks a sale as "pedido".

Revision ID: 035129fd211c
Revises: 49db50084b56
"""

import sqlalchemy as sa

from alembic import op

revision = "035129fd211c"
down_revision = "49db50084b56"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("part_sale_lines", sa.Column("dispatched_quantity", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("part_sale_lines", "dispatched_quantity")
