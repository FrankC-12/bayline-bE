"""A Tempario's own preview must show the parts' real cost under "Costo de
repuestos" (not an already-margined price), apply the margin exactly once,
and its total-with-IVA must match, cent for cent, what a real ODS charges
when it uses that same tempario — same formula, same default margin tier."""

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
from app.modules.post_ventas.models import LaborSettings, Tempario, TemparioPart
from app.modules.post_ventas.service import PostVentasService
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
        )
        session.add(order)
        session.commit()

        # MP-1: 1 hour of labor + 3 units of a part whose real cost is $24
        # (parts cost = 72.00, the exact figure from the acceptance criteria).
        tempario = Tempario(
            filial_id=filial_id, category=TemparioCategory.MANTENIMIENTO_PREVENTIVO, sequence_number=1,
            name="Mantenimiento preventivo", estimated_hours=1,
        )
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Filtro", price=0)
        session.add_all([tempario, part])
        session.commit()
        session.add(TemparioPart(tempario_id=tempario.id, part_id=part.id, name=part.name, quantity=3, unit_cost=24))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        session.add(warehouse)
        session.commit()
        session.add(
            PartLot(filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id, quantity_received=3, quantity_remaining=3, unit_cost=24)
        )
        session.commit()

        db = AsyncAdapter(session)
        yield PostVentasService(db), ServiceOrderService(db), order, tempario


@pytest.mark.asyncio
async def test_tempario_shows_real_cost_with_margin_applied_once(env):
    post_ventas, _service, _order, tempario = env
    read = await post_ventas.get_tempario(tempario.id)

    assert read.parts_cost == 72.0  # the real cost, not cost*1.30
    assert round(read.parts_margin, 2) == 21.6  # 72 * 0.30, applied once
    assert round(read.labor_cost, 2) == 25.0
    assert round(read.iva_amount, 2) == round((25.0 + 93.6) * 0.16, 2)
    assert round(read.total_with_iva, 2) == round(25.0 + 93.6 + (25.0 + 93.6) * 0.16, 2)


@pytest.mark.asyncio
async def test_tempario_total_with_iva_matches_the_ods_that_uses_it(env):
    post_ventas, service, order, tempario = env
    tempario_read = await post_ventas.get_tempario(tempario.id)

    await service.add_task(order.id, tempario.id)
    transfer = (await service.list_transfers(order.id))[0]
    await service.mark_transfer_ordered(transfer.id)

    order_summary = await service.get_order_summary(order.id)

    assert round(order_summary.total, 2) == round(tempario_read.total_with_iva, 2)
