import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from test_workshop_dispatch_postgres import inventory

from app.core.exceptions import BadRequestError, ConflictError
from app.core.idempotency import OperationReceipt
from app.modules.administracion.models import IncomeEntry
from app.modules.parts.models import Part
from app.modules.parts.schemas import PartSaleCreate, PartSalePaymentCreate
from app.modules.parts.service import PartsService
from app.modules.warehouse.models import PartLot, StockMovement
from app.modules.warehouse.schemas import LotLineInput, StockInCreate, StockOutCreate
from app.modules.warehouse.service import AlmacenService


@pytest.mark.asyncio
async def test_concurrent_stock_in_retries_create_only_one_batch(billing_db):
    ctx = billing_db
    part_id, warehouse_id, _ = await inventory(ctx, local=0)
    payload = StockInCreate(
        request_id=uuid.uuid4(),
        filial_id=ctx.filial_id,
        warehouse_id=warehouse_id,
        lines=[
            LotLineInput(part_id=part_id, quantity=3, unit_cost=10),
            LotLineInput(part_id=part_id, quantity=2, unit_cost=11),
        ],
    )

    async def send():
        async with ctx.sessions() as db:
            return [lot.id for lot in await AlmacenService(db).create_stock_in(payload)]

    first, retry = await asyncio.gather(send(), send())
    assert first == retry
    assert len(set(first)) == 2
    async with ctx.sessions() as db:
        assert (await db.get(Part, part_id)).stock_quantity == 5
        assert (
            await db.execute(
                select(func.count()).select_from(PartLot).where(PartLot.part_id == part_id)
            )
        ).scalar() == 2
        assert (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.part_id == part_id)
            )
        ).scalar() == 2
        changed = payload.model_copy(update={"reason": "Otro"})
        with pytest.raises(ConflictError):
            await AlmacenService(db).create_stock_in(changed)


@pytest.mark.asyncio
async def test_concurrent_stock_out_retries_consume_once_and_conflict_on_changed_data(billing_db):
    ctx = billing_db
    part_id, warehouse_id, _ = await inventory(ctx, local=10)
    payload = StockOutCreate(
        request_id=uuid.uuid4(),
        filial_id=ctx.filial_id,
        warehouse_id=warehouse_id,
        part_id=part_id,
        quantity=3,
    )

    async def send():
        async with ctx.sessions() as db:
            await AlmacenService(db).create_stock_out(payload)

    await asyncio.gather(send(), send())
    async with ctx.sessions() as db:
        assert (await db.get(Part, part_id)).stock_quantity == 7
        assert (
            await db.execute(
                select(func.sum(PartLot.quantity_remaining)).where(PartLot.part_id == part_id)
            )
        ).scalar() == 7
        assert (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.part_id == part_id)
            )
        ).scalar() == 1
        with pytest.raises(ConflictError):
            await AlmacenService(db).create_stock_out(payload.model_copy(update={"quantity": 2}))
        # A new intentional operation with identical data must still be possible.
        await AlmacenService(db).create_stock_out(
            payload.model_copy(update={"request_id": uuid.uuid4()})
        )
        assert (await db.get(Part, part_id)).stock_quantity == 4


@pytest.mark.asyncio
async def test_partial_parts_collection_retry_does_not_register_second_income(billing_db):
    ctx = billing_db
    part_id, warehouse_id, _ = await inventory(ctx, local=10)
    async with ctx.sessions() as db:
        sale = await PartsService(db).create_sale(
            PartSaleCreate(
                filial_id=ctx.filial_id,
                client_name="Cliente QA",
                warehouse_id=warehouse_id,
                lines=[{"part_id": part_id, "warehouse_id": warehouse_id, "quantity": 5}],
            )
        )
        sale_id = sale.id
    payload = PartSalePaymentCreate(
        request_id=uuid.uuid4(),
        payment_method="usd",
        paid_usd="20",
        paid_bs="0",
        usd_account_id=ctx.usd_account_id,
    )

    async def send():
        async with ctx.sessions() as db:
            response = await PartsService(db).collect_sale_payment(sale_id, payload, None)
            return response.amount_collected, response.pending_amount

    first, replay = await asyncio.gather(send(), send())
    assert first == replay
    assert first[1] > 0
    async with ctx.sessions() as db:
        assert (
            await db.execute(
                select(func.count())
                .select_from(IncomeEntry)
                .where(IncomeEntry.source_id == sale_id)
            )
        ).scalar() == 1
        with pytest.raises(ConflictError):
            await PartsService(db).collect_sale_payment(
                sale_id, payload.model_copy(update={"paid_usd": Decimal("10")}), None
            )
        assert await db.get(OperationReceipt, payload.request_id) is not None


@pytest.mark.asyncio
async def test_failed_inventory_operation_keeps_key_available_for_a_real_retry(billing_db):
    ctx = billing_db
    part_id, warehouse_id, _ = await inventory(ctx, local=2)
    payload = StockOutCreate(
        request_id=uuid.uuid4(),
        filial_id=ctx.filial_id,
        warehouse_id=warehouse_id,
        part_id=part_id,
        quantity=3,
    )
    async with ctx.sessions() as db:
        service = AlmacenService(db)
        with pytest.raises(BadRequestError):
            await service.create_stock_out(payload)
        assert await db.get(OperationReceipt, payload.request_id) is None
        assert (await db.get(Part, part_id)).stock_quantity == 2
        await service.create_stock_in(
            StockInCreate(
                filial_id=ctx.filial_id,
                warehouse_id=warehouse_id,
                lines=[LotLineInput(part_id=part_id, quantity=2, unit_cost=10)],
            )
        )
        await service.create_stock_out(payload)
        assert (await db.get(Part, part_id)).stock_quantity == 1
        assert await db.get(OperationReceipt, payload.request_id) is not None
        with pytest.raises(ConflictError):
            await service.create_stock_in(
                StockInCreate(
                    request_id=payload.request_id,
                    filial_id=ctx.filial_id,
                    warehouse_id=warehouse_id,
                    lines=[LotLineInput(part_id=part_id, quantity=2, unit_cost=10)],
                )
            )
