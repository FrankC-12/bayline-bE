import importlib.util
import uuid
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.core.search import fold_search


def test_migration_backfills_existing_client_names_in_batches():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/a4c93e682f17_client_order_pagination.py"
    )
    spec = importlib.util.spec_from_file_location("client_page_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    clients = sa.Table(
        "clients",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("filial_id", sa.UUID()),
        sa.Column("full_name", sa.String(150)),
    )
    sa.Table(
        "service_orders",
        metadata,
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("filial_id", sa.UUID()),
        sa.Column("technician_user_id", sa.UUID()),
        sa.Column("created_at", sa.DateTime()),
    )
    metadata.create_all(engine)
    names = ["José Ramírez", "ANDRÉS", "François", "Jose\u0301"] + [
        f"Cliente {index}" for index in range(501)
    ]
    with engine.begin() as connection:
        connection.execute(
            clients.insert(), [{"id": uuid.uuid4(), "full_name": name} for name in names]
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = connection.execute(sa.text("SELECT full_name, normalized_name FROM clients")).all()
        assert len(rows) == len(names)
        assert all(normalized == fold_search(name) for name, normalized in rows)
    engine.dispose()
