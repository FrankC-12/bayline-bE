"""Marking a service order as completado is rejected while a task is still
pendiente or an ODT hasn't been dispatched — the message says exactly what's
missing. The business can still force it through with an explicit
confirmation, which leaves who confirmed it and when."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.auth.schemas import CurrentUser
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import LaborSettings, Tempario, TemparioPart
from app.modules.roles.enums import RoleScope
from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus, TransferStatus
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import ServiceOrderUpdate
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.models import PartLot, Warehouse


def _user(filial_id) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), email="admin@test.com", role_id=uuid.uuid4(), role_slug="administrador",
        scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
    )


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.add(LaborSettings(filial_id=filial_id, hourly_rate=25, iva_percentage=16))

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
            status=ServiceOrderStatus.EN_PROGRESO,
        )
        session.add(order)
        session.commit()

        tempario = Tempario(
            filial_id=filial_id, category=TemparioCategory.FRENOS, sequence_number=1,
            name="Cambio de pastillas", estimated_hours=1,
        )
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Pastillas", price=0)
        session.add_all([tempario, part])
        session.commit()
        session.add(TemparioPart(tempario_id=tempario.id, part_id=part.id, name=part.name, quantity=1, unit_cost=10))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        session.add(warehouse)
        session.commit()
        session.add(
            PartLot(filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id, quantity_received=5, quantity_remaining=5, unit_cost=10)
        )
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), order, tempario, _user(filial_id)


@pytest.mark.asyncio
async def test_completing_with_a_pending_task_is_rejected(env):
    service, order, tempario, user = env
    await service.add_task(order.id, tempario.id)

    with pytest.raises(BadRequestError) as excinfo:
        await service.update_order(order.id, ServiceOrderUpdate(status=ServiceOrderStatus.COMPLETADO), user)

    assert "Cambio de pastillas" in str(excinfo.value)
    assert "ODT" in str(excinfo.value)


@pytest.mark.asyncio
async def test_completing_with_an_undispatched_odt_but_finished_tasks_is_rejected(env):
    service, order, tempario, user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)

    with pytest.raises(BadRequestError) as excinfo:
        await service.update_order(order.id, ServiceOrderUpdate(status=ServiceOrderStatus.COMPLETADO), user)

    assert "Cambio de pastillas" not in str(excinfo.value)
    assert "ODT" in str(excinfo.value)


@pytest.mark.asyncio
async def test_confirming_completes_the_order_and_records_who_and_when(env):
    service, order, tempario, user = env
    await service.add_task(order.id, tempario.id)

    completed = await service.update_order(
        order.id,
        ServiceOrderUpdate(status=ServiceOrderStatus.COMPLETADO, confirm_incomplete_completion=True),
        user,
    )

    assert completed.status == ServiceOrderStatus.COMPLETADO
    assert completed.completed_with_pending_items is True
    assert completed.completed_override_by_user_id == user.user_id
    assert completed.completed_override_at is not None


@pytest.mark.asyncio
async def test_completing_with_everything_finished_needs_no_confirmation(env):
    service, order, tempario, user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    transfer = (await service.list_transfers(order.id))[0]
    await service.mark_transfer_ordered(transfer.id)

    completed = await service.update_order(order.id, ServiceOrderUpdate(status=ServiceOrderStatus.COMPLETADO), user)

    assert completed.status == ServiceOrderStatus.COMPLETADO
    assert completed.completed_with_pending_items is False
    assert completed.completed_override_by_user_id is None
    assert completed.completed_override_at is None
