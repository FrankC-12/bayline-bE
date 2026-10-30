"""Vehicle opportunity pipeline and actual delivery evidence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
revision='d7ae60bc8548'
down_revision='c6fd59ab7437'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('vehicle_sales',sa.Column('delivered_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('vehicle_sales',sa.Column('delivered_by_user_id',UUID(as_uuid=True),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True))
    op.add_column('vehicle_sales',sa.Column('delivery_reference',sa.String(500),nullable=True))
    op.create_table('vehicle_opportunities',
        sa.Column('id',UUID(as_uuid=True),primary_key=True),
        sa.Column('filial_id',UUID(as_uuid=True),sa.ForeignKey('filiales.id',ondelete='CASCADE'),nullable=False),
        sa.Column('client_id',UUID(as_uuid=True),sa.ForeignKey('clients.id',ondelete='SET NULL'),nullable=True),
        sa.Column('client_name',sa.String(150),nullable=False),
        sa.Column('vehicle_id',UUID(as_uuid=True),sa.ForeignKey('dealership_vehicles.id',ondelete='SET NULL'),nullable=True),
        sa.Column('advisor_user_id',UUID(as_uuid=True),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
        sa.Column('sale_id',UUID(as_uuid=True),sa.ForeignKey('vehicle_sales.id',ondelete='SET NULL'),nullable=True,unique=True),
        sa.Column('origin',sa.String(30),nullable=False,server_default='registro_directo'),sa.Column('stage',sa.String(25),nullable=False,server_default='prospecto'),
        sa.Column('vehicle_label',sa.String(255),nullable=True),sa.Column('quoted_amount',sa.Numeric(18,2),nullable=True),sa.Column('quoted_currency',sa.String(3),nullable=False,server_default='USD'),
        sa.Column('sale_type',sa.String(15),nullable=False,server_default='contado'),sa.Column('quoted_at',sa.DateTime(timezone=True),nullable=True),
        sa.Column('notes',sa.Text(),nullable=True),sa.Column('next_contact_at',sa.Date(),nullable=True),sa.Column('lost_reason',sa.String(1000),nullable=True),
        sa.Column('activity',sa.JSON(),nullable=False,server_default='[]'),sa.Column('version',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('created_by_user_id',UUID(as_uuid=True),sa.ForeignKey('users.id',ondelete='SET NULL'),nullable=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    for field in ['filial_id','client_id','vehicle_id','advisor_user_id']:op.create_index(f'ix_vehicle_opportunities_{field}','vehicle_opportunities',[field])
    op.execute("INSERT INTO vehicle_opportunities (id,filial_id,client_id,client_name,vehicle_id,advisor_user_id,sale_id,created_at,updated_at) SELECT gen_random_uuid(),filial_id,client_id,client_name,vehicle_id,advisor_user_id,id,created_at,created_at FROM vehicle_sales")
    op.execute("INSERT INTO vehicle_opportunities (id,filial_id,client_id,client_name,vehicle_id,advisor_user_id,created_at,updated_at) SELECT gen_random_uuid(),v.filial_id,c.id,c.full_name,v.id,v.reserved_by_user_id,COALESCE(v.reserved_at,now()),now() FROM dealership_vehicles v JOIN clients c ON c.id=v.reserved_client_id WHERE v.status::text='RESERVADO'")
    op.execute("INSERT INTO vehicle_opportunities (id,filial_id,client_id,client_name,origin,vehicle_label,notes,next_contact_at) SELECT gen_random_uuid(),c.filial_id,c.id,c.full_name,c.lead_origin,c.lead_vehicle_interest,c.lead_notes,c.lead_next_contact_at FROM clients c WHERE c.lead_interest='vehiculo' AND c.lead_status!='descartado' AND NOT EXISTS (SELECT 1 FROM vehicle_opportunities o WHERE o.client_id=c.id)")

def downgrade():
    op.drop_table('vehicle_opportunities')
    for field in ['delivered_at','delivered_by_user_id','delivery_reference']:op.drop_column('vehicle_sales',field)
