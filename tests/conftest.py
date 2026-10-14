"""Keep PostgreSQL UUID columns textual in the SQLite test database."""

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.compiler import compiles


@compiles(UUID, "sqlite")
def sqlite_uuid_column(type_, compiler, **kwargs):
    # SQLite gives a declared UUID column NUMERIC affinity: hexadecimal IDs
    # resembling scientific notation can become floats (even infinity).
    # Production PostgreSQL retains its native UUID compilation unchanged.
    return "CHAR(32)"
