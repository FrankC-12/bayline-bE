"""Motivos de Entrada: a holding-wide catalog (same pattern as VehicleBrand),
never hard-deleted, names unique per holding."""

import os
import uuid

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.warehouse.exceptions import StockInReasonNameAlreadyExistsError
from app.modules.warehouse.schemas import StockInReasonCreate, StockInReasonUpdate
from app.modules.warehouse.service import DEFAULT_STOCK_IN_REASONS, AlmacenService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield AlmacenService(AsyncAdapter(session))


@pytest.mark.asyncio
async def test_seed_default_stock_in_reasons_creates_all_four(env):
    service = env
    holding_id = uuid.uuid4()

    await service.seed_default_stock_in_reasons(holding_id)

    reasons = await service.list_stock_in_reasons(holding_id)
    assert {r.name for r in reasons} == set(DEFAULT_STOCK_IN_REASONS)


@pytest.mark.asyncio
async def test_reasons_are_scoped_per_holding(env):
    service = env
    holding_a, holding_b = uuid.uuid4(), uuid.uuid4()
    await service.seed_default_stock_in_reasons(holding_a)

    assert await service.list_stock_in_reasons(holding_b) == []


@pytest.mark.asyncio
async def test_duplicate_name_within_holding_is_rejected(env):
    service = env
    holding_id = uuid.uuid4()
    await service.create_stock_in_reason(holding_id, StockInReasonCreate(name="Garantía"))

    with pytest.raises(StockInReasonNameAlreadyExistsError):
        await service.create_stock_in_reason(holding_id, StockInReasonCreate(name="garantía"))


@pytest.mark.asyncio
async def test_deactivated_reason_is_excluded_by_default_but_listable(env):
    service = env
    holding_id = uuid.uuid4()
    reason = await service.create_stock_in_reason(holding_id, StockInReasonCreate(name="Consignación"))

    await service.set_stock_in_reason_active(reason.id, holding_id, is_active=False)

    assert await service.list_stock_in_reasons(holding_id) == []
    all_reasons = await service.list_stock_in_reasons(holding_id, include_inactive=True)
    assert len(all_reasons) == 1
    assert all_reasons[0].is_active is False


@pytest.mark.asyncio
async def test_rename_reason(env):
    service = env
    holding_id = uuid.uuid4()
    reason = await service.create_stock_in_reason(holding_id, StockInReasonCreate(name="Ajuste"))

    updated = await service.update_stock_in_reason(reason.id, holding_id, StockInReasonUpdate(name="Ajuste de stock"))

    assert updated.name == "Ajuste de stock"
