"""While an ODT is still Pendiente, its lines' requested quantities can be
adjusted — e.g. an oil change line for 6 liters drops to 2 once the client
says they're bringing 4 of their own — or dropped entirely if the client
brings every unit themselves. Once the ODT is dispatched (Pedido), stock has
already been decremented against the original quantities and lines freeze."""

import os
import uuid
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.service_orders.exceptions import TransferLineNotEditableError, TransferNotFoundError
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.models import PartLot, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Aceite 20W50", price=10, stock_quantity=0)
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

        session.add(
            PartLot(
                filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
                quantity_received=6, quantity_remaining=6, unit_cost=5, received_at=datetime.now(UTC),
            )
        )
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, order, part


@pytest.mark.asyncio
async def test_reducing_the_quantity_while_pending_updates_price_and_allocations(env):
    service, session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 6)
    line = transfer.lines[0]

    updated = await service.set_transfer_line_quantity(line.id, 2)

    refreshed_line = updated.lines[0]
    assert refreshed_line.quantity == 2
    assert float(refreshed_line.line_total) == pytest.approx(2 * 5 * 1.30, rel=1e-3)


@pytest.mark.asyncio
async def test_reducing_quantity_frees_up_the_reserved_units_for_another_line(env):
    service, session, order, part = env
    first_transfer = await service.add_transfer_line(order.id, part.id, 6)
    line = first_transfer.lines[0]

    await service.set_transfer_line_quantity(line.id, 2)

    # The 4 units given back are now free for a second, unrelated line to claim.
    order2 = ServiceOrder(filial_id=order.filial_id, sequence_number=2, vehicle_id=order.vehicle_id)
    session.add(order2)
    session.commit()
    second_transfer = await service.add_transfer_line(order2.id, part.id, 4)
    assert second_transfer.lines[0].quantity == 4
    assert not (await service.get_order_summary(order2.id)).warnings


@pytest.mark.asyncio
async def test_removing_a_line_while_pending_deletes_it(env):
    service, session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 6)
    line = transfer.lines[0]

    updated = await service.remove_transfer_line(line.id)

    assert updated.lines == []


@pytest.mark.asyncio
async def test_cannot_change_quantity_once_the_odt_is_dispatched(env):
    service, session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 6)
    line = transfer.lines[0]
    await service.mark_transfer_ordered(transfer.id)

    with pytest.raises(TransferLineNotEditableError):
        await service.set_transfer_line_quantity(line.id, 2)


@pytest.mark.asyncio
async def test_cannot_remove_a_line_once_the_odt_is_dispatched(env):
    service, session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 6)
    line = transfer.lines[0]
    await service.mark_transfer_ordered(transfer.id)

    with pytest.raises(TransferLineNotEditableError):
        await service.remove_transfer_line(line.id)


@pytest.mark.asyncio
async def test_increasing_the_quantity_past_available_stock_warns_but_still_saves(env):
    service, session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 6)
    line = transfer.lines[0]

    updated = await service.set_transfer_line_quantity(line.id, 50)

    assert updated.lines[0].quantity == 50
    assert updated.stock_warnings
    assert "Aceite 20W50" in updated.stock_warnings[0]


@pytest.mark.asyncio
async def test_quantity_update_on_an_unknown_line_raises(env):
    service, _session, _order, _part = env
    with pytest.raises(TransferNotFoundError):
        await service.set_transfer_line_quantity(uuid.uuid4(), 1)
