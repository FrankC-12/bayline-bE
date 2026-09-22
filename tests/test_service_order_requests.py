"""Dispatched ODT parts requests from a service order ("Marcar como Pedido")
must be visible to almacén staff in the 'Órdenes de Transferencia' screen,
scoped by filial, and support an acknowledge step that drives the unseen
badge there."""

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
from app.modules.service_orders.enums import TransferStatus
from app.modules.service_orders.exceptions import TransferNotFoundError
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTransfer,
    ServiceOrderTransferLine,
    ServiceOrderTransferLotAllocation,
)
from app.modules.warehouse.models import Warehouse
from app.modules.warehouse.service import AlmacenService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="QA-002", name="Aceite 15W40", price=0)
        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add_all([part, client])
        session.commit()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
        session.add(vehicle)
        session.commit()
        yield AlmacenService(AsyncAdapter(session)), session, filial_id, part, vehicle


def _make_dispatched_transfer(session, part, vehicle, filial_id):
    order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    transfer = ServiceOrderTransfer(
        service_order_id=order.id, sequence_number=1, status=TransferStatus.PEDIDO,
        fulfilled_at=datetime.now(UTC),
    )
    session.add(transfer)
    session.flush()
    line = ServiceOrderTransferLine(transfer_id=transfer.id, part_id=part.id, quantity=2)
    session.add(line)
    session.commit()
    return order, transfer


@pytest.mark.asyncio
async def test_dispatched_request_is_listed_for_its_filial(env):
    service, session, filial_id, part, vehicle = env
    order, transfer = _make_dispatched_transfer(session, part, vehicle, filial_id)

    results = await service.list_service_order_requests(filial_id)

    assert len(results) == 1
    result = results[0]
    assert result.id == transfer.id
    assert result.service_order_code == order.code
    assert result.warehouse_seen is False
    assert result.vehicle_label == "Toyota Corolla · ABC123"
    assert len(result.lines) == 1
    assert result.lines[0].quantity == 2
    assert result.lines[0].part_code == "QA-002"


@pytest.mark.asyncio
async def test_request_shows_which_warehouse_each_line_was_dispatched_from(env):
    """A line isn't scoped to one warehouse — dispatch draws FIFO across
    every warehouse in the filial, so its quantity can split across more
    than one. Almacén staff must be able to see exactly where it's coming
    from, not just the part and total quantity."""
    service, session, filial_id, part, vehicle = env
    order, transfer = _make_dispatched_transfer(session, part, vehicle, filial_id)
    warehouse_a = Warehouse(filial_id=filial_id, name="Almacén Principal")
    warehouse_b = Warehouse(filial_id=filial_id, name="Almacén Norte")
    session.add_all([warehouse_a, warehouse_b])
    session.commit()
    line = transfer.lines[0]
    session.add_all(
        [
            ServiceOrderTransferLotAllocation(
                transfer_line_id=line.id, lot_id=uuid.uuid4(), warehouse_id=warehouse_a.id, quantity=1, unit_cost=10
            ),
            ServiceOrderTransferLotAllocation(
                transfer_line_id=line.id, lot_id=uuid.uuid4(), warehouse_id=warehouse_b.id, quantity=1, unit_cost=12
            ),
        ]
    )
    session.commit()
    # `line.allocations` (lazy="selectin") was already cached empty by the
    # `transfer.lines[0]` access above, before these rows existed — expire
    # it so the service's fresh query actually sees them, matching how a
    # real request (a separate call, separate session) always would.
    session.expire_all()

    results = await service.list_service_order_requests(filial_id)

    warehouses = {w.warehouse_name: w.quantity for w in results[0].lines[0].warehouses}
    assert warehouses == {"Almacén Principal": 1, "Almacén Norte": 1}


@pytest.mark.asyncio
async def test_request_with_no_recorded_allocations_shows_an_empty_warehouse_breakdown(env):
    """Pre-migration or otherwise allocation-less lines shouldn't crash the
    listing — they just show no warehouse breakdown."""
    service, session, filial_id, part, vehicle = env
    _make_dispatched_transfer(session, part, vehicle, filial_id)

    results = await service.list_service_order_requests(filial_id)

    assert results[0].lines[0].warehouses == []


@pytest.mark.asyncio
async def test_completing_a_request_updates_its_status_and_stays_listed(env):
    """A completed ODT stays visible (frozen counter, historical record) —
    only Pendiente ones are excluded."""
    service, session, filial_id, part, vehicle = env
    _order, transfer = _make_dispatched_transfer(session, part, vehicle, filial_id)
    completed_by = uuid.uuid4()

    await service.complete_service_order_request(transfer.id, completed_by)

    results = await service.list_service_order_requests(filial_id)
    assert results[0].status == "completado"
    assert results[0].completed_at is not None


@pytest.mark.asyncio
async def test_pending_transfer_is_excluded(env):
    service, session, filial_id, part, vehicle = env
    order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    transfer = ServiceOrderTransfer(service_order_id=order.id, sequence_number=1, status=TransferStatus.PENDIENTE)
    session.add(transfer)
    session.commit()

    assert await service.list_service_order_requests(filial_id) == []


@pytest.mark.asyncio
async def test_scoped_to_filial(env):
    service, session, filial_id, part, vehicle = env
    _make_dispatched_transfer(session, part, vehicle, filial_id)

    assert await service.list_service_order_requests(uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_acknowledge_marks_seen(env):
    service, session, filial_id, part, vehicle = env
    _order, transfer = _make_dispatched_transfer(session, part, vehicle, filial_id)

    await service.acknowledge_service_order_request(transfer.id)

    results = await service.list_service_order_requests(filial_id)
    assert results[0].warehouse_seen is True


@pytest.mark.asyncio
async def test_acknowledge_raises_when_not_found(env):
    service, _session, _filial_id, _part, _vehicle = env

    with pytest.raises(TransferNotFoundError):
        await service.acknowledge_service_order_request(uuid.uuid4())
