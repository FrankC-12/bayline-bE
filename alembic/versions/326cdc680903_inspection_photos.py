"""Fotos en Inspecciones — inspection_damages.photo_url (una foto por daño
marcado) y preliminary_inspections.photo_urls (galería general, mismo
patrón ya usado por PartReturn.photo_urls).

Revision ID: 326cdc680903
Revises: 0d840cd6b68c
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "326cdc680903"
down_revision: str | Sequence[str] | None = "0d840cd6b68c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("inspection_damages", sa.Column("photo_url", sa.String(500), nullable=True))
    op.add_column(
        "preliminary_inspections",
        sa.Column("photo_urls", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("preliminary_inspections", "photo_urls")
    op.drop_column("inspection_damages", "photo_url")
