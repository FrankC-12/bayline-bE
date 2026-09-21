"""Client search must match regardless of accents — "jose"/"JOSE" should
find "José Ramírez" just as typing the accent would."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client
from app.modules.clients.service import ClientService
from app.modules.filiales.models import Filial


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.add(Client(
            filial_id=filial_id, full_name="José Ramírez", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        ))
        session.add(Client(
            filial_id=filial_id, full_name="Maria Perez", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="87654321", phone_primary="04121234568", address="Caracas",
        ))
        session.commit()
        yield ClientService(AsyncAdapter(session)), filial_id


@pytest.mark.asyncio
async def test_search_without_accents_matches_accented_name(env):
    service, filial_id = env

    results = await service.list_clients(filial_id, search="jose")

    assert [c.full_name for c in results] == ["José Ramírez"]


@pytest.mark.asyncio
async def test_search_with_accents_still_matches(env):
    service, filial_id = env

    results = await service.list_clients(filial_id, search="José")

    assert [c.full_name for c in results] == ["José Ramírez"]


@pytest.mark.asyncio
async def test_search_is_case_insensitive_and_accent_insensitive_together(env):
    service, filial_id = env

    results = await service.list_clients(filial_id, search="RAMIREZ")

    assert [c.full_name for c in results] == ["José Ramírez"]


@pytest.mark.asyncio
async def test_search_without_accented_name_does_not_match_unrelated_client(env):
    service, filial_id = env

    results = await service.list_clients(filial_id, search="jose")

    assert "Maria Perez" not in [c.full_name for c in results]
