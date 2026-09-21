"""A cédula/RIF can only be registered once per filial. The app-level check
(_ensure_document_is_available) is only a fast path — the real backstop is
the DB-level unique constraint, so a race (e.g. two concurrent requests from
a double-click) can't slip two clients through with the same document."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.exceptions import DocumentAlreadyExistsError
from app.modules.clients.models import Client
from app.modules.clients.schemas import ClientCreate
from app.modules.clients.service import ClientService
from app.modules.filiales.models import Filial


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.commit()
        yield ClientService(AsyncAdapter(session)), session, filial_id


def _payload(filial_id, document_number="12345678"):
    return ClientCreate(
        filial_id=filial_id,
        full_name="Cliente de Prueba",
        client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V,
        document_number=document_number,
        phone_primary="04121234567",
        address="Caracas",
    )


@pytest.mark.asyncio
async def test_creating_a_client_with_a_duplicate_document_is_rejected_in_spanish(env):
    service, _session, filial_id = env
    await service.create_client(_payload(filial_id))

    with pytest.raises(DocumentAlreadyExistsError) as excinfo:
        await service.create_client(_payload(filial_id))

    assert "cédula/RIF" in str(excinfo.value)
    assert "12345678" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_different_filial_may_reuse_the_same_document(env):
    service, session, filial_id = env
    other_filial_id = uuid.uuid4()
    session.add(Filial(id=other_filial_id, holding_id=uuid.uuid4(), name="Otra filial", slug="otra"))
    session.commit()

    await service.create_client(_payload(filial_id))
    other = await service.create_client(_payload(other_filial_id))
    assert other.document_number == "12345678"


def test_the_database_itself_rejects_a_duplicate_document_per_filial(env):
    """The real backstop: even bypassing the service entirely, the unique
    constraint fires — this is what closes the double-click / concurrent
    request race the app-level check alone can't."""
    _service, session, filial_id = env
    session.add(
        Client(
            filial_id=filial_id, full_name="A", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="99999999", phone_primary="04121234567",
            address="Caracas",
        )
    )
    session.commit()

    session.add(
        Client(
            filial_id=filial_id, full_name="B", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="99999999", phone_primary="04121234567",
            address="Caracas",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.asyncio
async def test_a_race_that_slips_past_the_app_level_check_still_gets_a_clean_spanish_error(
    env, monkeypatch
):
    """Simulates two concurrent creates both passing _ensure_document_is_available
    before either commits — the DB constraint (not the SELECT check) is what
    actually stops the second one, and it must surface as the same clean
    domain error, not a raw IntegrityError."""
    service, _session, filial_id = env
    await service.create_client(_payload(filial_id, document_number="55555555"))

    async def _no_check(*_args, **_kwargs):
        return None

    monkeypatch.setattr(service, "_ensure_document_is_available", _no_check)

    with pytest.raises(DocumentAlreadyExistsError):
        await service.create_client(_payload(filial_id, document_number="55555555"))
