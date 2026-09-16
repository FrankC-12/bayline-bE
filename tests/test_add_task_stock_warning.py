"""Adding a task (tempario) to a service order must never be blocked by the
stock of its catalog-linked parts — a client may bring their own part, or
the shop may request it from another branch later. It should still warn,
though, so the advisor knows to request it from almacén. Only dispatch
("pedir a almacén") actually blocks on stock."""

import uuid

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
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import Tempario, TemparioPart
from app.modules.service_orders.models import ServiceOrder
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

        client = Client(
            filial_id=filial_id, full_name="Cliente", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add(client)
        session.commit()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123")
        session.add(vehicle)
        session.commit()

        order = ServiceOrder(
            filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id, advisor_user_id=uuid.uuid4(),
        )
        session.add(order)
        session.commit()

        tempario = Tempario(
            filial_id=filial_id, category=TemparioCategory.MOTOR, sequence_number=1,
            name="Cambio de filtro", estimated_hours=1,
        )
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Filtro de aceite", price=10)
        session.add_all([tempario, part])
        session.commit()
        session.add(TemparioPart(tempario_id=tempario.id, part_id=part.id, name=part.name, quantity=3, unit_cost=5))
        session.commit()

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        session.add(warehouse)
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, order, tempario, part, warehouse


@pytest.mark.asyncio
async def test_task_is_created_even_when_its_linked_part_has_no_stock_at_all(env):
    service, _session, order, tempario, _part, _warehouse = env

    task = await service.add_task(order.id, tempario.id)

    assert task.id is not None
    assert len(task.stock_warnings) == 1
    assert "insuficiente" in task.stock_warnings[0].lower()


@pytest.mark.asyncio
async def test_task_is_created_with_no_warning_when_stock_fully_covers_it(env):
    service, session, order, tempario, part, warehouse = env
    session.add(
        PartLot(
            filial_id=order.filial_id, warehouse_id=warehouse.id, part_id=part.id,
            quantity_received=10, quantity_remaining=10, unit_cost=5,
        )
    )
    session.commit()

    task = await service.add_task(order.id, tempario.id)

    assert task.stock_warnings == []


@pytest.mark.asyncio
async def test_task_is_created_with_a_warning_when_stock_partially_covers_it(env):
    service, session, order, tempario, part, warehouse = env
    session.add(
        PartLot(
            filial_id=order.filial_id, warehouse_id=warehouse.id, part_id=part.id,
            quantity_received=1, quantity_remaining=1, unit_cost=5,
        )
    )
    session.commit()

    task = await service.add_task(order.id, tempario.id)

    assert len(task.stock_warnings) == 1
    assert "disponible 1 de 3" in task.stock_warnings[0]


@pytest.mark.asyncio
async def test_requesting_the_short_part_from_almacen_is_what_actually_blocks(env):
    service, session, order, tempario, part, warehouse = env
    task = await service.add_task(order.id, tempario.id)
    assert task.stock_warnings

    transfers = await service.list_transfers(order.id)
    transfer = transfers[0]

    with pytest.raises(InsufficientStockError):
        await service.mark_transfer_ordered(transfer.id)
