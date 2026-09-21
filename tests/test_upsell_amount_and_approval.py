"""An upsell now carries a real amount (built from tempario tasks and
parts) and, on approval, adds those same tasks/parts to the ODS — the order
recalculates its own total automatically since nothing is cached. Approval
also records who approved it and through what channel."""

import uuid

import pytest
from pydantic import ValidationError
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
from app.modules.post_ventas.models import LaborSettings, Tempario, TemparioPart
from app.modules.service_orders.enums import UpsellStatus
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import (
    UpsellCreate,
    UpsellDecisionInput,
    UpsellPartInput,
    UpsellTaskInput,
)
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.models import PartLot, Warehouse


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

        order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id, advisor_user_id=uuid.uuid4())
        session.add(order)
        session.commit()

        tempario = Tempario(
            filial_id=filial_id, category=TemparioCategory.FRENOS, sequence_number=1,
            name="Cambio de pastillas", estimated_hours=2,
        )
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Pastillas", price=0)
        session.add_all([tempario, part])
        session.commit()
        session.add(TemparioPart(tempario_id=tempario.id, part_id=part.id, name=part.name, quantity=1, unit_cost=20))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        session.add(warehouse)
        session.commit()
        session.add(
            PartLot(filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id, quantity_received=5, quantity_remaining=5, unit_cost=20)
        )
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, order, tempario, part


@pytest.mark.asyncio
async def test_creating_an_upsell_with_no_lines_is_rejected():
    with pytest.raises(ValidationError):
        UpsellCreate(title="Extra", description="Trabajo adicional")


@pytest.mark.asyncio
async def test_approving_without_a_channel_is_rejected():
    with pytest.raises(ValidationError):
        UpsellDecisionInput(status="aprobado")


@pytest.mark.asyncio
async def test_upsell_amount_reflects_its_tasks_and_parts(env):
    service, _session, order, tempario, part = env
    upsell = await service.create_upsell(
        order.id,
        UpsellCreate(
            title="Frenos desgastados", description="Se notó desgaste en las pastillas.",
            tasks=[UpsellTaskInput(tempario_id=tempario.id)],
            parts=[UpsellPartInput(part_id=part.id, quantity=2)],
        ),
    )
    # labor: 2h * $25 = 50; parts: 2 * $20 * 1.30 (default margin) = 52
    assert upsell.amount == 102.0
    assert upsell.tasks[0].tempario_id == tempario.id
    assert upsell.parts[0].line_total == 52.0


@pytest.mark.asyncio
async def test_approving_an_upsell_adds_its_lines_and_recalculates_the_order_total(env):
    service, _session, order, tempario, part = env
    upsell = await service.create_upsell(
        order.id,
        UpsellCreate(
            title="Frenos desgastados", description="Se notó desgaste en las pastillas.",
            tasks=[UpsellTaskInput(tempario_id=tempario.id)],
        ),
    )
    before = await service.get_order_summary(order.id)
    assert before.total == 0

    admin_id = uuid.uuid4()
    decided = await service.decide_upsell(
        upsell.id, UpsellDecisionInput(status="aprobado", approval_channel="presencial"), admin_id
    )

    assert decided.status == UpsellStatus.APROBADO
    assert decided.approved_by_user_id == admin_id
    assert decided.approval_channel == "presencial"

    tasks = await service.list_tasks(order.id)
    assert len(tasks) == 1
    assert tasks[0].tempario_id == tempario.id

    after = await service.get_order_summary(order.id)
    # 2h * $25 labor + (1 * $20 * 1.30) parts pulled in by the tempario's own
    # linked part, plus 16% IVA on top (LaborSettings default in this fixture).
    subtotal = 50 + 26.0
    assert after.total == pytest.approx(subtotal * 1.16)


@pytest.mark.asyncio
async def test_rejecting_an_upsell_does_not_touch_the_order(env):
    service, _session, order, tempario, _part = env
    upsell = await service.create_upsell(
        order.id, UpsellCreate(title="Extra", description="Trabajo adicional", tasks=[UpsellTaskInput(tempario_id=tempario.id)])
    )
    decided = await service.decide_upsell(upsell.id, UpsellDecisionInput(status="rechazado"), uuid.uuid4())
    assert decided.status == UpsellStatus.RECHAZADO
    assert decided.approved_by_user_id is None
    assert await service.list_tasks(order.id) == []


@pytest.mark.asyncio
async def test_list_upsells_includes_both_approved_and_rejected(env):
    service, _session, order, tempario, _part = env
    approved = await service.create_upsell(
        order.id, UpsellCreate(title="Upsell A", description="Aprobado", tasks=[UpsellTaskInput(tempario_id=tempario.id)])
    )
    rejected = await service.create_upsell(
        order.id, UpsellCreate(title="Upsell B", description="Rechazado", tasks=[UpsellTaskInput(tempario_id=tempario.id)])
    )
    await service.decide_upsell(approved.id, UpsellDecisionInput(status="aprobado", approval_channel="whatsapp"), uuid.uuid4())
    await service.decide_upsell(rejected.id, UpsellDecisionInput(status="rechazado"), uuid.uuid4())

    upsells = await service.list_upsells(order.filial_id)
    statuses = {u.id: u.status for u in upsells}
    assert statuses[approved.id] == UpsellStatus.APROBADO
    assert statuses[rejected.id] == UpsellStatus.RECHAZADO
