"""External vehicle financing snapshots, independent of integration transport."""
from alembic import op
import sqlalchemy as sa
revision='e2a7619c4b83'
down_revision='c8e3147f2a90'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('vehicle_sales',sa.Column('financing_snapshot',sa.JSON(),nullable=True))
    op.add_column('vehicle_sales',sa.Column('financing_updated_at',sa.DateTime(timezone=True),nullable=True))

def downgrade():
    op.drop_column('vehicle_sales','financing_updated_at')
    op.drop_column('vehicle_sales','financing_snapshot')
