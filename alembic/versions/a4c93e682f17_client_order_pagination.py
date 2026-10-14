"""Normalized client search and indexes for client/order pages."""

import unicodedata

import sqlalchemy as sa

from alembic import op

revision = "a4c93e682f17"
down_revision = "f3b7821a9d65"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "clients", sa.Column("normalized_name", sa.Text(), nullable=False, server_default="")
    )
    table = sa.table(
        "clients",
        sa.column("id", sa.UUID()),
        sa.column("full_name", sa.String()),
        sa.column("normalized_name", sa.String()),
    )
    connection = op.get_bind()
    last = None
    while True:
        query = sa.select(table.c.id, table.c.full_name).order_by(table.c.id).limit(500)
        if last is not None:
            query = query.where(table.c.id > last)
        rows = connection.execute(query).all()
        if not rows:
            break
        values = [
            {
                "client_key": row.id,
                "folded_name": "".join(
                    c
                    for c in unicodedata.normalize("NFKD", row.full_name)
                    if not unicodedata.combining(c)
                ).lower(),
            }
            for row in rows
        ]
        connection.execute(
            table.update()
            .where(table.c.id == sa.bindparam("client_key"))
            .values(normalized_name=sa.bindparam("folded_name")),
            values,
        )
        last = rows[-1].id
    op.create_index("ix_clients_filial_name_id", "clients", ["filial_id", "full_name", "id"])
    op.create_index(
        "ix_service_orders_filial_chronology", "service_orders", ["filial_id", "created_at", "id"]
    )
    op.create_index(
        "ix_service_orders_technician_chronology",
        "service_orders",
        ["filial_id", "technician_user_id", "created_at", "id"],
    )


def downgrade():
    op.drop_index("ix_service_orders_technician_chronology", table_name="service_orders")
    op.drop_index("ix_service_orders_filial_chronology", table_name="service_orders")
    op.drop_index("ix_clients_filial_name_id", table_name="clients")
    op.drop_column("clients", "normalized_name")
