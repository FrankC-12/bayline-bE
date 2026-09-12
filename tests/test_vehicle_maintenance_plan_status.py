"""Vehicle <-> Plan assignment and the live-computed per-entry status
(pendiente/vencido/cumplido/omitido) — pieces 3 and 4 of the plan module."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.clients.enums import ClientType, DocumentType, MaintenancePlanEntryStatus
from app.modules.clients.models import Client, Vehicle
from app.modules.clients.service import ClientService
from app.modules.inspections.models import PreliminaryInspection
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import MaintenancePlan, MaintenancePlanEntry, Tempario
from app.modules.service_orders.enums import ServiceOrderStatus, TaskStatus
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask


@pytest.fixture
def fleet():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        other_filial_id = uuid.uuid4()

        client = Client(
            filial_id=filial_id,
            full_name="Cliente Uno",
            client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V,
            document_number="AB123CD",
            phone_primary="04141234567",
            address="Av. Principal",
        )
        session.add(client)
        session.flush()
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="AB123CD")
        session.add(vehicle)
        session.flush()

        tempario_5k = Tempario(
            filial_id=filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=501,
            name="Servicio 5.000 km",
            estimated_hours=1,
        )
        tempario_10k = Tempario(
            filial_id=filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=502,
            name="Servicio 10.000 km",
            estimated_hours=1,
        )
        tempario_6m = Tempario(
            filial_id=filial_id,
            category=TemparioCategory.MANTENIMIENTO_PREVENTIVO,
            sequence_number=503,
            name="Revisión semestral",
            estimated_hours=1,
        )
        session.add_all([tempario_5k, tempario_10k, tempario_6m])
        session.flush()

        plan = MaintenancePlan(filial_id=filial_id, brand="Toyota", name="Plan Toyota")
        session.add(plan)
        session.flush()
        entry_5k = MaintenancePlanEntry(plan_id=plan.id, tempario_id=tempario_5k.id, interval_km=5000)
        entry_10k = MaintenancePlanEntry(plan_id=plan.id, tempario_id=tempario_10k.id, interval_km=10000)
        entry_6m = MaintenancePlanEntry(plan_id=plan.id, tempario_id=tempario_6m.id, interval_months=6)
        session.add_all([entry_5k, entry_10k, entry_6m])

        foreign_plan = MaintenancePlan(filial_id=other_filial_id, brand="Toyota", name="Plan ajeno")
        session.add(foreign_plan)
        session.commit()

        yield {
            "session": session,
            "filial_id": filial_id,
            "client": client,
            "vehicle": vehicle,
            "tempario_5k": tempario_5k,
            "tempario_10k": tempario_10k,
            "tempario_6m": tempario_6m,
            "plan": plan,
            "entry_5k": entry_5k,
            "entry_10k": entry_10k,
            "entry_6m": entry_6m,
            "foreign_plan": foreign_plan,
        }
    engine.dispose()


def _entry_status(statuses, entry_id):
    return next(e for e in statuses.entries if e.entry_id == entry_id).status


@pytest.mark.asyncio
async def test_no_plan_assigned_returns_empty_status(fleet):
    service = ClientService(AsyncAdapter(fleet["session"]))

    status_read = await service.get_vehicle_plan_status(fleet["vehicle"].id)

    assert status_read.plan_id is None
    assert status_read.entries == []


@pytest.mark.asyncio
async def test_assign_plan_rejects_foreign_filial(fleet):
    service = ClientService(AsyncAdapter(fleet["session"]))

    with pytest.raises(BadRequestError):
        await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["foreign_plan"].id)

    fleet["session"].refresh(fleet["vehicle"])
    assert fleet["vehicle"].maintenance_plan_id is None


@pytest.mark.asyncio
async def test_all_entries_pendiente_when_new_and_below_threshold(fleet):
    fleet["vehicle"].purchase_date = date.today()
    fleet["vehicle"].mileage = 0
    fleet["session"].commit()
    service = ClientService(AsyncAdapter(fleet["session"]))

    status_read = await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["plan"].id)

    assert status_read.plan_id == fleet["plan"].id
    assert status_read.plan_brand == "Toyota"
    assert len(status_read.entries) == 3
    assert all(e.status == MaintenancePlanEntryStatus.PENDIENTE for e in status_read.entries)


@pytest.mark.asyncio
async def test_entry_is_vencido_once_mileage_passes_its_threshold(fleet):
    fleet["vehicle"].purchase_date = date.today()
    fleet["vehicle"].mileage = 6000
    fleet["session"].commit()
    service = ClientService(AsyncAdapter(fleet["session"]))

    status_read = await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["plan"].id)

    assert _entry_status(status_read, fleet["entry_5k"].id) == MaintenancePlanEntryStatus.VENCIDO
    assert _entry_status(status_read, fleet["entry_10k"].id) == MaintenancePlanEntryStatus.PENDIENTE


@pytest.mark.asyncio
async def test_entry_is_vencido_once_months_since_purchase_pass(fleet):
    fleet["vehicle"].purchase_date = date.today() - timedelta(days=200)  # > 6 months
    fleet["vehicle"].mileage = 0
    fleet["session"].commit()
    service = ClientService(AsyncAdapter(fleet["session"]))

    status_read = await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["plan"].id)

    assert _entry_status(status_read, fleet["entry_6m"].id) == MaintenancePlanEntryStatus.VENCIDO


@pytest.mark.asyncio
async def test_entry_is_cumplido_when_a_completed_task_matches_its_tempario(fleet):
    session = fleet["session"]
    fleet["vehicle"].purchase_date = date.today()
    fleet["vehicle"].mileage = 6000
    order = ServiceOrder(
        filial_id=fleet["filial_id"],
        vehicle_id=fleet["vehicle"].id,
        sequence_number=1001,
        status=ServiceOrderStatus.ORDEN_CERRADA,
        closed_at=datetime.now(UTC),
    )
    session.add(order)
    session.flush()
    session.add(
        ServiceOrderTask(
            service_order_id=order.id,
            tempario_id=fleet["tempario_5k"].id,
            code_snapshot="MP-501",
            name_snapshot="Servicio 5.000 km",
            hours_snapshot=1,
            status=TaskStatus.COMPLETADA,
        )
    )
    session.commit()
    service = ClientService(AsyncAdapter(session))

    status_read = await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["plan"].id)

    entry = next(e for e in status_read.entries if e.entry_id == fleet["entry_5k"].id)
    assert entry.status == MaintenancePlanEntryStatus.CUMPLIDO
    assert entry.completed_service_order_code == "ODS-1001"
    assert entry.completed_at is not None


@pytest.mark.asyncio
async def test_entry_is_omitido_when_a_later_entry_was_done_instead(fleet):
    session = fleet["session"]
    fleet["vehicle"].purchase_date = date.today()
    fleet["vehicle"].mileage = 0  # Never actually reached 5,000 km.
    order = ServiceOrder(
        filial_id=fleet["filial_id"],
        vehicle_id=fleet["vehicle"].id,
        sequence_number=1002,
        status=ServiceOrderStatus.ORDEN_CERRADA,
        closed_at=datetime.now(UTC),
    )
    session.add(order)
    session.flush()
    session.add(
        ServiceOrderTask(
            service_order_id=order.id,
            tempario_id=fleet["tempario_10k"].id,
            code_snapshot="MP-502",
            name_snapshot="Servicio 10.000 km",
            hours_snapshot=1,
            status=TaskStatus.COMPLETADA,
        )
    )
    session.commit()
    service = ClientService(AsyncAdapter(session))

    status_read = await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["plan"].id)

    assert _entry_status(status_read, fleet["entry_5k"].id) == MaintenancePlanEntryStatus.OMITIDO
    assert _entry_status(status_read, fleet["entry_10k"].id) == MaintenancePlanEntryStatus.CUMPLIDO


@pytest.mark.asyncio
async def test_current_mileage_prefers_latest_inspection_over_registration_mileage(fleet):
    session = fleet["session"]
    fleet["vehicle"].purchase_date = date.today()
    fleet["vehicle"].mileage = 1000
    session.add(
        PreliminaryInspection(
            filial_id=fleet["filial_id"],
            vehicle_id=fleet["vehicle"].id,
            inspector_user_id=uuid.uuid4(),
            mileage=6000,
        )
    )
    session.commit()
    service = ClientService(AsyncAdapter(session))

    status_read = await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["plan"].id)

    assert status_read.current_mileage == 6000
    assert _entry_status(status_read, fleet["entry_5k"].id) == MaintenancePlanEntryStatus.VENCIDO


@pytest.mark.asyncio
async def test_reference_date_falls_back_to_created_at_without_purchase_date(fleet):
    service = ClientService(AsyncAdapter(fleet["session"]))

    status_read = await service.assign_maintenance_plan(fleet["vehicle"].id, fleet["plan"].id)

    assert status_read.reference_date == fleet["vehicle"].created_at.date()
