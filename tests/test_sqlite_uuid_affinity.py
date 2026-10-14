import uuid

from sqlalchemy import Column, MetaData, Table, create_engine, select
from sqlalchemy.dialects.postgresql import UUID


def test_sqlite_preserves_native_uuid_values_that_look_like_scientific_notation():
    engine = create_engine("sqlite://")
    table = Table(
        "uuid_affinity_test", MetaData(), Column("id", UUID(as_uuid=True), primary_key=True)
    )
    table.metadata.create_all(engine)
    identifier = uuid.UUID("1e000000000000000000000000000000")
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=identifier))
        assert connection.execute(select(table.c.id)).scalar_one() == identifier
    engine.dispose()
