"""'Mantenimiento por vencer' — advisor-suggested next-visit date on Vehicle,
set at ODS close time and surfaced through the KPIs maintenance-due report."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.kpis.service import KpiService
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def workshop():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()

        def make_vehicle(plate: str, phone: str = "04141234567") -> Vehicle:
            client = Client(
                filial_id=filial_id,
                full_name=f"Cliente {plate}",
                client_type=ClientType.PARTICULAR,
                document_type=DocumentType.V,
                document_number=plate,
                phone_primary=phone,
                address="Av. Principal",
            )
            session.add(client)
            session.flush()
            vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate=plate)
            session.add(vehicle)
            session.flush()
            return vehicle

        vehicle = make_vehicle("AB123CD")
        order = ServiceOrder(
            filial_id=filial_id,
            vehicle_id=vehicle.id,
            sequence_number=1,
            status=ServiceOrderStatus.COMPLETADO,
            invoiced_at=datetime.now(UTC),
        )
        session.add(order)
        session.commit()
        yield filial_id, session, order, vehicle, make_vehicle
    engine.dispose()


@pytest.mark.asyncio
async def test_close_order_sets_vehicle_next_maintenance_due_at(workshop):
    _filial_id, session, order, vehicle, _make = workshop
    service = ServiceOrderService(AsyncAdapter(session))
    due = date.today() + timedelta(days=180)

    closed = await service.close_order(order.id, due)

    assert closed.status == ServiceOrderStatus.ORDEN_CERRADA
    session.refresh(vehicle)
    assert vehicle.next_maintenance_due_at == due


@pytest.mark.asyncio
async def test_close_order_without_a_date_leaves_vehicle_unchanged(workshop):
    _filial_id, session, order, vehicle, _make = workshop
    vehicle.next_maintenance_due_at = date(2026, 1, 1)
    session.commit()
    service = ServiceOrderService(AsyncAdapter(session))

    await service.close_order(order.id, None)

    session.refresh(vehicle)
    assert vehicle.next_maintenance_due_at == date(2026, 1, 1)


@pytest.mark.asyncio
async def test_maintenance_due_windows_and_overdue_count(workshop):
    filial_id, session, _order, in_window_vehicle, make_vehicle = workshop
    today = date.today()
    in_window_vehicle.next_maintenance_due_at = today + timedelta(days=10)

    far_vehicle = make_vehicle("EF456GH")
    far_vehicle.next_maintenance_due_at = today + timedelta(days=45)

    overdue_vehicle = make_vehicle("IJ789KL")
    overdue_vehicle.next_maintenance_due_at = today - timedelta(days=5)

    make_vehicle("MN012OP")  # No suggested date at all — never applies.
    session.commit()

    service = KpiService(AsyncAdapter(session))

    report_30 = await service.get_maintenance_due(filial_id, 30)
    assert [row.vehicle_id for row in report_30.rows] == [in_window_vehicle.id]
    assert report_30.overdue_count == 1

    report_60 = await service.get_maintenance_due(filial_id, 60)
    assert {row.vehicle_id for row in report_60.rows} == {in_window_vehicle.id, far_vehicle.id}
    assert report_60.overdue_count == 1

    row = next(r for r in report_30.rows if r.vehicle_id == in_window_vehicle.id)
    assert row.plate == "AB123CD"
    assert row.phone_primary == "04141234567"
    assert row.days_until_due == 10


@pytest.mark.asyncio
async def test_maintenance_due_is_scoped_to_filial(workshop):
    filial_id, session, _order, vehicle, _make = workshop
    vehicle.next_maintenance_due_at = date.today() + timedelta(days=5)
    session.commit()
    service = KpiService(AsyncAdapter(session))

    report = await service.get_maintenance_due(uuid.uuid4(), 30)

    assert report.rows == []
    assert report.overdue_count == 0
