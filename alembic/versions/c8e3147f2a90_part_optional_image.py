"""Optional catalog part image.

Revision ID: c8e3147f2a90
Revises: b5d10c73a982
"""
from alembic import op
import sqlalchemy as sa
revision = "c8e3147f2a90"
down_revision = "b5d10c73a982"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("parts", sa.Column("image_url", sa.String(1024), nullable=True))

def downgrade():
    op.drop_column("parts", "image_url")
