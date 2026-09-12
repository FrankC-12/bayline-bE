"""Adding a part to a service order must price it via real FIFO lot
consumption (oldest lot first, filial-wide — same scope dispatch already
used), exactly like the counter-sale flow already does — never off a
single "latest" lot. Reported bug: with an older $20 lot and a newer $12
lot, a fresh service order quoted $15.60 (the $12 lot + 30%) for 1 unit
instead of $26.00 (the $20 lot + 30%).

Concrete required numbers, with 5 units @ $20 (older) and 10 units @ $12
(newer) available: requesting 1 unit -> $26.00; requesting 7 -> $161.20
((5*20 + 2*12) * 1.30)."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTransferLotAllocation
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.exceptions import InsufficientStockError
from app.modules.warehouse.models import PartLot, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        part = Part(filial_id=filial_id, code="P-1", name="Alternador", price=10, stock_quantity=15)
        session.add_all([warehouse, part])

        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add(client)
        session.commit()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
        session.add(vehicle)
        session.commit()

        order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
        session.add(order)
        session.commit()

        older = PartLot(
            filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
            quantity_received=5, quantity_remaining=5, unit_cost=20,
            received_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        newer = PartLot(
            filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
            quantity_received=10, quantity_remaining=10, unit_cost=12,
            received_at=datetime(2026, 9, 9, tzinfo=UTC),
        )
        session.add_all([older, newer])
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, order, part, warehouse, older, newer


@pytest.mark.asyncio
async def test_requesting_one_unit_prices_from_the_older_lot(env):
    service, session, order, part, _warehouse, _older, _newer = env
    transfer = await service.add_transfer_line(order.id, part.id, 1)
    line = transfer.lines[0]
    assert float(line.unit_price) == 26.00
    assert float(line.line_total) == 26.00


@pytest.mark.asyncio
async def test_requesting_seven_units_blends_both_lots(env):
    service, session, order, part, _warehouse, _older, _newer = env
    transfer = await service.add_transfer_line(order.id, part.id, 7)
    line = transfer.lines[0]
    assert float(line.line_total) == 161.20


@pytest.mark.asyncio
async def test_topping_up_the_same_line_recomputes_the_full_cumulative_fifo(env):
    service, session, order, part, _warehouse, _older, _newer = env
    await service.add_transfer_line(order.id, part.id, 1)
    transfer = await service.add_transfer_line(order.id, part.id, 6)
    line = transfer.lines[0]
    assert line.quantity == 7
    assert float(line.line_total) == 161.20


@pytest.mark.asyncio
async def test_allocations_recorded_at_add_time_before_dispatch(env):
    service, session, order, part, warehouse, older, newer = env
    transfer = await service.add_transfer_line(order.id, part.id, 7)
    line = transfer.lines[0]

    allocations = session.scalars(
        select(ServiceOrderTransferLotAllocation).where(
            ServiceOrderTransferLotAllocation.transfer_line_id == line.id
        )
    ).all()
    by_lot = {a.lot_id: a for a in allocations}
    assert by_lot[older.id].quantity == 5
    assert by_lot[newer.id].quantity == 2
    assert all(a.warehouse_id == warehouse.id for a in allocations)
    # Nothing is actually consumed until dispatch.
    session.refresh(older)
    session.refresh(newer)
    assert older.quantity_remaining == 5
    assert newer.quantity_remaining == 10


@pytest.mark.asyncio
async def test_requesting_more_than_available_raises(env):
    service, session, order, part, _warehouse, _older, _newer = env
    with pytest.raises(InsufficientStockError):
        await service.add_transfer_line(order.id, part.id, 16)


@pytest.mark.asyncio
async def test_dispatch_consumes_the_previewed_lots_and_matches_the_add_time_price(env):
    service, session, order, part, warehouse, older, newer = env
    transfer = await service.add_transfer_line(order.id, part.id, 7)
    await service.mark_transfer_ordered(transfer.id)

    session.refresh(older)
    session.refresh(newer)
    assert older.quantity_remaining == 0
    assert newer.quantity_remaining == 8

    session.expire_all()
    line = session.get(type(transfer.lines[0]), transfer.lines[0].id)
    assert float(line.line_total) == 161.20


@pytest.mark.asyncio
async def test_dispatch_reprices_if_stock_shrank_since_the_line_was_added(env):
    service, session, order, part, warehouse, older, newer = env
    transfer = await service.add_transfer_line(order.id, part.id, 1)
    line_id = transfer.lines[0].id
    assert float(transfer.lines[0].line_total) == 26.00

    # Another order took the older $20 lot in the meantime.
    older.quantity_remaining = 0
    session.commit()

    await service.mark_transfer_ordered(transfer.id)
    session.expire_all()

    from app.modules.service_orders.models import ServiceOrderTransferLine

    line = session.get(ServiceOrderTransferLine, line_id)
    # Now only the $12 lot is available, so the real dispatch price differs
    # from the original preview.
    assert float(line.line_total) == 15.60
