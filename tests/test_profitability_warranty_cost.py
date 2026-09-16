"""'Costo de garantía (taller)' in the departmentalized Rentabilidad report:
what rework claims actually cost the workshop — real FIFO part cost (traced
via F0-01's lot allocations) plus labor at the current hourly rate. Free to
the client, not to the shop. Surfaced as its own adjustment row rather than
folded into a department."""

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.administracion.service import AdministracionService
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.parts.models import Part
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import LaborSettings, Tempario
from app.modules.service_orders.enums import ReworkFailureCategory
from app.modules.service_orders.models import ReworkClaim, ServiceOrder
from app.modules.service_orders.service import ServiceOrderService
from app.modules.warehouse.models import PartLot, Warehouse


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))
        session.add(LaborSettings(filial_id=filial_id, hourly_rate=20))

        warehouse = Warehouse(filial_id=filial_id, name="Principal")
        part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Alternador", price=10, stock_quantity=0)
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

        db = AsyncAdapter(session)
        yield AdministracionService(db), ServiceOrderService(db), session, filial_id, order, part, warehouse


def make_lot(session, filial_id, warehouse, part, quantity, unit_cost):
    lot = PartLot(
        filial_id=filial_id, warehouse_id=warehouse.id, part_id=part.id,
        quantity_received=quantity, quantity_remaining=quantity, unit_cost=unit_cost,
        received_at=datetime.now(timezone.utc),
    )
    session.add(lot)
    part.stock_quantity += quantity
    session.commit()
    return lot


def warranty_cost_of(report) -> float:
    row = next(a for a in report.adjustments if a.key == "costo_garantia_taller")
    return -row.amount  # stored signed (negative, reduces net profit); the tests want the magnitude


@pytest.mark.asyncio
async def test_no_claims_means_zero_warranty_cost(env):
    admin, _so, _session, filial_id, _order, _part, _wh = env
    report = await admin.get_profitability(filial_id, date(2026, 1, 1), date(2026, 12, 31))
    assert warranty_cost_of(report) == 0
    assert report.net_profit == report.gross_profit_total


@pytest.mark.asyncio
async def test_part_cost_uses_the_real_fifo_allocation(env):
    admin, so, session, filial_id, order, part, warehouse = env
    make_lot(session, filial_id, warehouse, part, quantity=10, unit_cost=8)

    transfer = await so.add_transfer_line(order.id, part.id, 3)
    await so.mark_transfer_ordered(transfer.id)

    session.add(ReworkClaim(
        filial_id=filial_id, service_order_id=order.id, part_id=part.id,
        failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO,
        failure_cause="Falló", claimed_at=date(2026, 6, 15),
    ))
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))
    assert warranty_cost_of(report) == pytest.approx(24.0)  # 3 * 8


@pytest.mark.asyncio
async def test_part_cost_falls_back_to_line_cost_total_without_an_allocation(env):
    admin, session_holder, session, filial_id, order, part, _warehouse = env
    from app.modules.service_orders.models import ServiceOrderTransfer, ServiceOrderTransferLine

    transfer = ServiceOrderTransfer(service_order_id=order.id, sequence_number=1)
    session.add(transfer)
    session.commit()
    session.add(ServiceOrderTransferLine(transfer_id=transfer.id, part_id=part.id, quantity=2, cost_total=50))
    session.add(ReworkClaim(
        filial_id=filial_id, service_order_id=order.id, part_id=part.id,
        failure_category=ReworkFailureCategory.REPUESTO_DEFECTUOSO,
        failure_cause="Anterior a F0-01", claimed_at=date(2026, 6, 15),
    ))
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))
    assert warranty_cost_of(report) == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_labor_cost_uses_tempario_hours_times_hourly_rate(env):
    admin, _so, session, filial_id, order, _part, _warehouse = env
    tempario = Tempario(
        filial_id=filial_id, category=TemparioCategory.MOTOR, sequence_number=1,
        name="Cambio de aceite", estimated_hours=2,
    )
    session.add(tempario)
    session.commit()
    session.add(ReworkClaim(
        filial_id=filial_id, service_order_id=order.id, tempario_id=tempario.id,
        failure_category=ReworkFailureCategory.MANO_DE_OBRA,
        failure_cause="Mal ajustado", claimed_at=date(2026, 6, 15),
    ))
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))
    assert warranty_cost_of(report) == pytest.approx(40.0)  # 2h * $20/h
    assert report.net_profit == pytest.approx(report.gross_profit_total - 40.0)


@pytest.mark.asyncio
async def test_claims_outside_the_period_are_excluded(env):
    admin, _so, session, filial_id, order, _part, _warehouse = env
    tempario = Tempario(
        filial_id=filial_id, category=TemparioCategory.MOTOR, sequence_number=1,
        name="Cambio de aceite", estimated_hours=2,
    )
    session.add(tempario)
    session.commit()
    session.add(ReworkClaim(
        filial_id=filial_id, service_order_id=order.id, tempario_id=tempario.id,
        failure_category=ReworkFailureCategory.MANO_DE_OBRA,
        failure_cause="Fuera de rango", claimed_at=date(2026, 5, 1),
    ))
    session.commit()

    report = await admin.get_profitability(filial_id, date(2026, 6, 1), date(2026, 6, 30))
    assert warranty_cost_of(report) == 0
