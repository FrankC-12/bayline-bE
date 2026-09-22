"""An ODT gains a third status, Completado, confirmed from the almacén
side once parts are physically handed over to the técnico — this is what
lets the elapsed-time counter running since 'Pedido' pause instead of
counting forever."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.models import Vehicle
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.service_orders.enums import TransferStatus
from app.modules.service_orders.exceptions import InvalidTransferStatusTransitionError, TransferNotFoundError
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
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Filtro", price=10, stock_quantity=0)
        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add_all([warehouse, part, client])
        session.commit()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
        session.add(vehicle)
        session.commit()
        order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
        session.add(order)
        session.add(
            PartLot(
                filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
                quantity_received=5, quantity_remaining=5, unit_cost=Decimal("10.00"),
            )
        )
        session.commit()
        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, order, part


@pytest.mark.asyncio
async def test_completing_a_dispatched_odt_sets_status_and_timestamps(env):
    service, _session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 2)
    await service.mark_transfer_ordered(transfer.id)
    completed_by = uuid.uuid4()

    completed = await service.complete_transfer(transfer.id, completed_by)

    assert completed.status == TransferStatus.COMPLETADO
    assert completed.completed_by_user_id == completed_by
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_cannot_complete_an_odt_still_pendiente(env):
    service, _session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 2)

    with pytest.raises(InvalidTransferStatusTransitionError):
        await service.complete_transfer(transfer.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_cannot_complete_an_already_completed_odt(env):
    service, _session, order, part = env
    transfer = await service.add_transfer_line(order.id, part.id, 2)
    await service.mark_transfer_ordered(transfer.id)
    await service.complete_transfer(transfer.id, uuid.uuid4())

    with pytest.raises(InvalidTransferStatusTransitionError):
        await service.complete_transfer(transfer.id, uuid.uuid4())


@pytest.mark.asyncio
async def test_completing_an_unknown_transfer_raises_not_found(env):
    service, _session, _order, _part = env

    with pytest.raises(TransferNotFoundError):
        await service.complete_transfer(uuid.uuid4(), uuid.uuid4())
