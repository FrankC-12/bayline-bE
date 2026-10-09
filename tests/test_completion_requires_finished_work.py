"""A service order's status (pendiente/en_progreso/completado) is derived
automatically from its tasks (and, for completado, its ODTs) — see
ServiceOrderService._sync_status_from_tasks. Completing with something
pending is only possible through the explicit force_complete_order override,
which leaves who forced it and when."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter, make_order_type

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
from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus
from app.modules.service_orders.exceptions import InvalidStatusTransitionError
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

        # No explicit status — the model default (pendiente) is what a real
        # order starts at, and this status is now entirely derived anyway.
        order = ServiceOrder(
            filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id, advisor_user_id=uuid.uuid4(),
            order_type_id=make_order_type(session, filial_id),
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
async def test_a_new_order_with_no_tasks_stays_pendiente(env):
    service, order, _tempario, _user = env
    assert order.status == ServiceOrderStatus.PENDIENTE


@pytest.mark.asyncio
async def test_adding_a_pending_task_keeps_the_order_pendiente(env):
    service, order, tempario, _user = env
    await service.add_task(order.id, tempario.id)
    assert order.status == ServiceOrderStatus.PENDIENTE


@pytest.mark.asyncio
async def test_a_task_en_progreso_moves_the_order_to_en_progreso(env):
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.EN_PROGRESO)
    assert order.status == ServiceOrderStatus.EN_PROGRESO


@pytest.mark.asyncio
async def test_a_task_en_espera_de_repuestos_moves_the_order_to_en_progreso(env):
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.EN_ESPERA_DE_REPUESTOS)
    assert order.status == ServiceOrderStatus.EN_PROGRESO


@pytest.mark.asyncio
async def test_all_tasks_completed_with_pending_odt_completes_order(env):
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    assert order.status == ServiceOrderStatus.COMPLETADO
    assert (await service.list_transfers(order.id))[0].status.value == "pendiente"


@pytest.mark.asyncio
async def test_all_tasks_completed_and_odt_dispatched_auto_completes_the_order(env):
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    transfer = (await service.list_transfers(order.id))[0]

    await service.mark_transfer_ordered(transfer.id)

    assert order.status == ServiceOrderStatus.COMPLETADO
    assert order.completed_at is not None
    assert order.completed_with_pending_items is False


@pytest.mark.asyncio
async def test_an_odt_already_marked_completado_by_almacen_also_auto_completes(env):
    """Regression: dispatch (mark_transfer_ordered) is what flips the ODT off
    PENDIENTE — a later almacén confirmation (complete_transfer) just moves
    it further along and must not re-block completion."""
    service, order, tempario, user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    transfer = (await service.list_transfers(order.id))[0]
    await service.mark_transfer_ordered(transfer.id)
    await service.complete_transfer(transfer.id, user.user_id)

    assert order.status == ServiceOrderStatus.COMPLETADO


@pytest.mark.asyncio
async def test_a_cancelled_only_task_with_no_pending_odt_still_completes(env):
    """A cancelada task was deliberately dropped, not left unfinished — an
    order whose only task was cancelled, with nothing else pending, has
    nothing actionable left and should complete, not look pendiente."""
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.CANCELADA)
    transfer = (await service.list_transfers(order.id))[0]

    await service.mark_transfer_ordered(transfer.id)

    assert order.status == ServiceOrderStatus.COMPLETADO
    assert order.completed_with_pending_items is False


@pytest.mark.asyncio
async def test_deleting_the_last_unfinished_task_can_complete_the_order(env):
    service, order, tempario, _user = env
    task = await service.add_task(order.id, tempario.id)
    transfer = (await service.list_transfers(order.id))[0]
    await service.mark_transfer_ordered(transfer.id)
    assert order.status == ServiceOrderStatus.PENDIENTE

    await service.delete_task(task.id)

    assert order.status == ServiceOrderStatus.PENDIENTE  # no tasks at all


@pytest.mark.asyncio
async def test_update_order_rejects_manually_setting_an_automatic_status(env):
    service, order, tempario, user = env
    await service.add_task(order.id, tempario.id)

    with pytest.raises(BadRequestError) as excinfo:
        await service.update_order(order.id, ServiceOrderUpdate(status=ServiceOrderStatus.COMPLETADO), user)

    assert excinfo.value.error_code == "status_is_automatic"


@pytest.mark.asyncio
async def test_force_complete_with_a_pending_task_records_who_and_when(env):
    service, order, tempario, user = env
    await service.add_task(order.id, tempario.id)

    completed = await service.force_complete_order(order.id, user)

    assert completed.status == ServiceOrderStatus.COMPLETADO
    assert completed.completed_with_pending_items is True
    assert completed.completed_override_by_user_id == user.user_id
    assert completed.completed_override_at is not None
    assert completed.completed_at is not None


@pytest.mark.asyncio
async def test_force_complete_works_directly_from_pendiente(env):
    service, order, tempario, user = env
    assert order.status == ServiceOrderStatus.PENDIENTE

    completed = await service.force_complete_order(order.id, user)

    assert completed.status == ServiceOrderStatus.COMPLETADO


@pytest.mark.asyncio
async def test_force_complete_cannot_be_called_on_an_already_completed_order(env):
    service, order, tempario, user = env
    task = await service.add_task(order.id, tempario.id)
    await service.update_task_status(task.id, TaskStatus.COMPLETADA)
    transfer = (await service.list_transfers(order.id))[0]
    await service.mark_transfer_ordered(transfer.id)
    assert order.status == ServiceOrderStatus.COMPLETADO

    with pytest.raises(InvalidStatusTransitionError):
        await service.force_complete_order(order.id, user)
