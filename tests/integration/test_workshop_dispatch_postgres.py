"""Real inventory reservations, routing, dispatch and retry behavior."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.exceptions import BadRequestError
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part, PartCategory
from app.modules.service_orders.dispatch import WorkshopDispatchService
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTransferLotAllocation,
)
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.enums import TransferStatus
from app.modules.warehouse.models import PartLot, StockMovement, Transfer, Warehouse
from app.modules.warehouse.reservations import reserved_by_lot
from app.modules.warehouse.service import AlmacenService


async def inventory(ctx, *, local=5, remote=0):
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        category = PartCategory(holding_id=filial.holding_id, name="QA")
        destination = Warehouse(filial_id=ctx.filial_id, name="P2P", is_workshop_default=True)
        source = Warehouse(filial_id=ctx.filial_id, name="Mostrador")
        db.add_all([category, destination, source])
        await db.flush()
        part = Part(
            filial_id=ctx.filial_id,
            category_id=category.id,
            code="QA",
            name="Aceite",
            stock_quantity=local + remote,
            price=13,
        )
        db.add(part)
        await db.flush()
        for warehouse, quantity in ((destination, local), (source, remote)):
            if quantity:
                db.add(
                    PartLot(
                        filial_id=ctx.filial_id,
                        warehouse_id=warehouse.id,
                        part_id=part.id,
                        quantity_received=quantity,
                        quantity_remaining=quantity,
                        unit_cost=10,
                    )
                )
        await db.commit()
        return part.id, destination.id, source.id


@pytest.mark.asyncio
async def test_submission_reserves_without_consumption_and_dispatch_retries_once(billing_db):
    ctx = billing_db
    part_id, _, _ = await inventory(ctx)
    async with ctx.sessions() as db:
        request = await ServiceOrderService(db).add_transfer_line(ctx.order_id, part_id, 3)
        request_id = request.id
        await WorkshopDispatchService(db).submit(request.id)
        assert (await db.get(Part, part_id)).stock_quantity == 5
        assert (
            await db.execute(
                select(func.sum(PartLot.quantity_remaining)).where(PartLot.part_id == part_id)
            )
        ).scalar() == 5
        assert (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.part_id == part_id)
            )
        ).scalar() == 0
        for allocation in (await db.execute(select(ServiceOrderTransferLotAllocation))).scalars():
            allocation.created_at = datetime.now(UTC) - timedelta(days=3)
        await db.commit()
        assert sum((await reserved_by_lot(db, part_id)).values()) == 3

    async def complete():
        async with ctx.sessions() as db:
            await WorkshopDispatchService(db).dispatch(request_id)

    await asyncio.gather(complete(), complete())
    async with ctx.sessions() as db:
        assert (await db.get(Part, part_id)).stock_quantity == 2
        assert (
            await db.execute(
                select(func.sum(PartLot.quantity_remaining)).where(PartLot.part_id == part_id)
            )
        ).scalar() == 2
        assert (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.part_id == part_id)
            )
        ).scalar() == 1
        assert await reserved_by_lot(db, part_id) == {}


@pytest.mark.asyncio
async def test_other_warehouse_generates_transfer_and_blocks_dispatch_until_received(billing_db):
    ctx = billing_db
    part_id, destination, source = await inventory(ctx, local=2, remote=3)
    async with ctx.sessions() as db:
        request = await ServiceOrderService(db).add_transfer_line(ctx.order_id, part_id, 4)
        await WorkshopDispatchService(db).submit(request.id)
        request_id = request.id
        transfer = (
            await db.execute(select(Transfer).where(Transfer.workshop_request_id == request_id))
        ).scalar_one()
        transfer_id = transfer.id
        assert transfer.origin_warehouse_id == source
        assert transfer.destination_warehouse_id == destination
        assert (await db.get(Part, part_id)).stock_quantity == 5
        with pytest.raises(BadRequestError, match="traslados pendientes"):
            await WorkshopDispatchService(db).dispatch(request_id)
        await AlmacenService(db).update_transfer_status(transfer_id, TransferStatus.EN_PROCESO)
        await AlmacenService(db).update_transfer_status(transfer_id, TransferStatus.COMPLETADA)
        await WorkshopDispatchService(db).dispatch(request_id)
        assert (await db.get(Part, part_id)).stock_quantity == 1
        assert (
            await db.execute(
                select(func.sum(PartLot.quantity_remaining)).where(PartLot.part_id == part_id)
            )
        ).scalar() == 1


@pytest.mark.asyncio
async def test_shortfall_is_submitted_and_never_creates_negative_stock(billing_db):
    ctx = billing_db
    part_id, _, _ = await inventory(ctx, local=2)
    async with ctx.sessions() as db:
        request = await ServiceOrderService(db).add_transfer_line(ctx.order_id, part_id, 4)
        await WorkshopDispatchService(db).submit(request.id)
        request_id = request.id
        assert request.backorder_notified_at is not None
        assert request.lines[0].shortfall_quantity == 2
        with pytest.raises(BadRequestError):
            await WorkshopDispatchService(db).dispatch(request_id)
        part = await db.get(Part, part_id)
        assert part.stock_quantity == 2


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_reserve_the_same_units(billing_db):
    ctx = billing_db
    part_id, _, _ = await inventory(ctx, local=5)
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        other = ServiceOrder(
            filial_id=ctx.filial_id,
            vehicle_id=order.vehicle_id,
            order_type_id=order.order_type_id,
            sequence_number=2,
        )
        db.add(other)
        await db.commit()
        first = await ServiceOrderService(db).add_transfer_line(ctx.order_id, part_id, 4)
        second = await ServiceOrderService(db).add_transfer_line(other.id, part_id, 4)
        ids = [first.id, second.id]

    async def submit(request_id):
        async with ctx.sessions() as db:
            await WorkshopDispatchService(db).submit(request_id)

    await asyncio.gather(*(submit(request_id) for request_id in ids))
    async with ctx.sessions() as db:
        assert sum((await reserved_by_lot(db, part_id)).values()) == 5
        assert (await db.get(Part, part_id)).stock_quantity == 5


@pytest.mark.asyncio
async def test_reserved_stock_is_unavailable_to_sales_and_manual_outs(billing_db):
    from app.modules.parts.schemas import PartSaleQuoteInput
    from app.modules.parts.service import PartsService
    from app.modules.warehouse.exceptions import InsufficientStockError

    ctx = billing_db
    part_id, destination, _ = await inventory(ctx, local=5)
    async with ctx.sessions() as db:
        request = await ServiceOrderService(db).add_transfer_line(ctx.order_id, part_id, 3)
        await WorkshopDispatchService(db).submit(request.id)
        with pytest.raises(InsufficientStockError):
            await PartsService(db).quote_sale(
                PartSaleQuoteInput(
                    filial_id=ctx.filial_id,
                    warehouse_id=destination,
                    lines=[{"part_id": part_id, "quantity": 3}],
                )
            )
        with pytest.raises(InsufficientStockError):
            await AlmacenService(db)._consume_fifo(destination, part_id, 3)
        assert (await db.get(Part, part_id)).stock_quantity == 5


@pytest.mark.asyncio
async def test_same_part_under_two_payers_does_not_count_reservations_twice(billing_db):
    from app.modules.service_orders.enums import ServiceOrderPayer

    ctx = billing_db
    part_id, _, _ = await inventory(ctx, local=5)
    async with ctx.sessions() as db:
        request = await ServiceOrderService(db).add_transfer_line(ctx.order_id, part_id, 2)
        await ServiceOrderService(db).add_transfer_line(
            ctx.order_id, part_id, 3, payer=ServiceOrderPayer.GARANTIA_TALLER
        )
        await WorkshopDispatchService(db).submit(request.id)
        assert sum((await reserved_by_lot(db, part_id)).values()) == 5
        await WorkshopDispatchService(db).dispatch(request.id)
        assert (await db.get(Part, part_id)).stock_quantity == 0


@pytest.mark.asyncio
async def test_legacy_request_is_not_deducted_a_second_time(billing_db):
    from app.modules.service_orders.enums import TransferStatus as RequestStatus

    ctx = billing_db
    part_id, _, _ = await inventory(ctx, local=5)
    async with ctx.sessions() as db:
        request = await ServiceOrderService(db).add_transfer_line(ctx.order_id, part_id, 3)
        request.status = RequestStatus.PEDIDO
        request.stock_deducted = True  # A pre-migration request already dispatched.
        await db.commit()
        await WorkshopDispatchService(db).dispatch(request.id)
        assert (await db.get(Part, part_id)).stock_quantity == 5
        assert (
            await db.execute(
                select(func.count())
                .select_from(StockMovement)
                .where(StockMovement.part_id == part_id)
            )
        ).scalar() == 0
