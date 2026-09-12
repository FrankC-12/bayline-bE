"""Each service order task and transfer line marks who pays for it — the
customer, the shop's own workshop warranty, the factory warranty, a
maintenance plan, or a supplier. This is what lets one order be "mixed":
some lines billed to the client, others not. The order summary's client-
facing subtotal/total only reflect `cliente`-payer lines; everything else
is rolled into `non_client_subtotal` for visibility, not billed."""

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
from app.modules.service_orders.enums import ServiceOrderPayer
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
            name="Cambio de aceite", estimated_hours=2,
        )
        part = Part(filial_id=filial_id, code="P-1", name="Filtro", price=10, stock_quantity=0)
        session.add_all([tempario, part])
        session.commit()

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        session.add(warehouse)
        session.commit()
        session.add(
            PartLot(
                filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
                quantity_received=100, quantity_remaining=100, unit_cost=5,
            )
        )
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, order, tempario, part


@pytest.mark.asyncio
async def test_add_task_defaults_to_cliente(env):
    service, _session, order, tempario, _part = env
    task = await service.add_task(order.id, tempario.id)
    assert task.payer == ServiceOrderPayer.CLIENTE


@pytest.mark.asyncio
async def test_garantia_taller_task_excluded_from_client_total(env):
    service, _session, order, tempario, _part = env
    await service.add_task(order.id, tempario.id, payer=ServiceOrderPayer.GARANTIA_TALLER)

    summary = await service.get_order_summary(order.id)

    assert summary.labor_subtotal == 0
    assert summary.total == 0
    assert summary.non_client_subtotal > 0


@pytest.mark.asyncio
async def test_mixed_order_splits_client_and_non_client_totals(env):
    service, _session, order, tempario, _part = env
    tempario2 = Tempario(
        filial_id=order.filial_id, category=TemparioCategory.MOTOR, sequence_number=2,
        name="Alineación", estimated_hours=1,
    )
    _session.add(tempario2)
    _session.commit()

    await service.add_task(order.id, tempario.id, payer=ServiceOrderPayer.CLIENTE)
    await service.add_task(order.id, tempario2.id, payer=ServiceOrderPayer.GARANTIA_FABRICA)

    summary = await service.get_order_summary(order.id)

    assert summary.labor_subtotal == 25.0 * 2  # only the cliente-payer task's hours
    assert summary.non_client_subtotal == 25.0 * 1
    assert summary.total > 0


@pytest.mark.asyncio
async def test_auto_added_linked_part_inherits_task_payer(env):
    service, session, order, tempario, part = env
    session.add(TemparioPart(tempario_id=tempario.id, part_id=part.id, name=part.name, quantity=2, unit_cost=5))
    session.commit()

    await service.add_task(order.id, tempario.id, payer=ServiceOrderPayer.PROVEEDOR)

    transfers = await service.list_transfers(order.id)
    lines = [line for tr in transfers for line in tr.lines]
    assert len(lines) == 1
    assert lines[0].payer == ServiceOrderPayer.PROVEEDOR


@pytest.mark.asyncio
async def test_same_part_different_payers_stay_separate_lines(env):
    service, _session, order, _tempario, part = env
    await service.add_transfer_line(order.id, part.id, 1, payer=ServiceOrderPayer.CLIENTE)
    await service.add_transfer_line(order.id, part.id, 3, payer=ServiceOrderPayer.PLAN_MANTENIMIENTO)

    transfers = await service.list_transfers(order.id)
    lines = [line for tr in transfers for line in tr.lines]
    assert len(lines) == 2
    quantities_by_payer = {line.payer: line.quantity for line in lines}
    assert quantities_by_payer[ServiceOrderPayer.CLIENTE] == 1
    assert quantities_by_payer[ServiceOrderPayer.PLAN_MANTENIMIENTO] == 3


@pytest.mark.asyncio
async def test_update_task_payer_moves_amount_between_totals(env):
    service, _session, order, tempario, _part = env
    task = await service.add_task(order.id, tempario.id)
    before = await service.get_order_summary(order.id)
    assert before.labor_subtotal > 0
    assert before.non_client_subtotal == 0

    await service.update_task_payer(task.id, ServiceOrderPayer.GARANTIA_TALLER)

    after = await service.get_order_summary(order.id)
    assert after.labor_subtotal == 0
    assert after.non_client_subtotal == before.labor_subtotal


@pytest.mark.asyncio
async def test_update_transfer_line_payer_moves_amount_between_totals(env):
    service, _session, order, _tempario, part = env
    await service.add_transfer_line(order.id, part.id, 1)
    transfers = await service.list_transfers(order.id)
    line_id = transfers[0].lines[0].id

    before = await service.get_order_summary(order.id)
    assert before.non_client_subtotal == 0

    await service.update_transfer_line_payer(line_id, ServiceOrderPayer.PROVEEDOR)

    after = await service.get_order_summary(order.id)
    assert after.parts_subtotal == 0
    assert after.non_client_subtotal == before.parts_subtotal
