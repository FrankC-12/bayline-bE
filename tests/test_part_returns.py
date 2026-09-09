"""Regression tests: a part returned usado/defectuoso must never be allowed
back into sellable inventory — only a write-off (Baja/merma) destination —
and every return requires at least one evidence photo."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.parts.enums import ReturnCondition, ReturnReason
from app.modules.parts.exceptions import MissingReturnPhotoError
from app.modules.parts.models import Part
from app.modules.parts.schemas import PartReturnCreate
from app.modules.parts.service import PartsService

PHOTO = ["/api/v1/uploads/part-returns/evidence.jpg"]


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        part = Part(
            id=uuid.uuid4(), filial_id=filial_id, code="P1", name="Repuesto", price=10, stock_quantity=5
        )
        session.add(part)
        session.commit()
        yield PartsService(AsyncAdapter(session)), session, filial_id, part


def _payload(filial_id, part_id, condition, destination, photo_urls=PHOTO):
    return PartReturnCreate(
        filial_id=filial_id,
        part_id=part_id,
        condition=condition,
        origin_warehouse="Almacén 1",
        destination_warehouse=destination,
        quantity=1,
        reason=ReturnReason.OTRO,
        photo_urls=photo_urls,
    )


@pytest.mark.asyncio
async def test_nuevo_can_return_to_sellable_inventory(env):
    service, session, filial_id, part = env
    await service.create_return(
        _payload(filial_id, part.id, ReturnCondition.NUEVO, "Almacén 1"), uuid.uuid4()
    )
    session.refresh(part)
    assert part.stock_quantity == 6


@pytest.mark.asyncio
async def test_defectuoso_to_sellable_inventory_is_rejected(env):
    service, _session, filial_id, part = env
    with pytest.raises(BadRequestError):
        await service.create_return(
            _payload(filial_id, part.id, ReturnCondition.DEFECTUOSO, "Almacén 1"), uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_usado_to_sellable_inventory_is_rejected(env):
    service, _session, filial_id, part = env
    with pytest.raises(BadRequestError):
        await service.create_return(
            _payload(filial_id, part.id, ReturnCondition.USADO, "Almacén 1"), uuid.uuid4()
        )


@pytest.mark.asyncio
async def test_defectuoso_to_write_off_is_allowed_and_does_not_restock(env):
    service, session, filial_id, part = env
    await service.create_return(
        _payload(filial_id, part.id, ReturnCondition.DEFECTUOSO, "Baja (merma)"), uuid.uuid4()
    )
    session.refresh(part)
    assert part.stock_quantity == 5


@pytest.mark.asyncio
async def test_usado_to_write_off_is_allowed_and_does_not_restock(env):
    service, session, filial_id, part = env
    await service.create_return(
        _payload(filial_id, part.id, ReturnCondition.USADO, "Baja (merma)"), uuid.uuid4()
    )
    session.refresh(part)
    assert part.stock_quantity == 5


@pytest.mark.asyncio
async def test_rejected_return_is_not_persisted(env):
    service, session, filial_id, part = env
    with pytest.raises(BadRequestError):
        await service.create_return(
            _payload(filial_id, part.id, ReturnCondition.USADO, "Almacén 1"), uuid.uuid4()
        )
    assert await service.list_returns(filial_id) == []


@pytest.mark.asyncio
async def test_return_without_photos_is_rejected(env):
    service, _session, filial_id, part = env
    with pytest.raises(MissingReturnPhotoError):
        await service.create_return(
            _payload(filial_id, part.id, ReturnCondition.NUEVO, "Almacén 1", photo_urls=[]),
            uuid.uuid4(),
        )


@pytest.mark.asyncio
async def test_return_with_photos_persists_them(env):
    service, _session, filial_id, part = env
    ret = await service.create_return(
        _payload(filial_id, part.id, ReturnCondition.NUEVO, "Almacén 1"), uuid.uuid4()
    )
    assert ret.photo_urls == PHOTO
