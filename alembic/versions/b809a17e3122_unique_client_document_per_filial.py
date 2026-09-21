"""a cédula/RIF can only be registered once per filial — the app-level check
alone left a race (two concurrent creates could both pass it), so this adds
the real backstop at the database level

Revision ID: b809a17e3122
Revises: 4c47352daeff
Create Date: 2026-09-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b809a17e3122"
down_revision: str | Sequence[str] | None = "4c47352daeff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_clients_filial_document", "clients", ["filial_id", "document_number"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_clients_filial_document", "clients", type_="unique")
