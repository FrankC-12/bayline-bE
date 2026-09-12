"""Every time an order's invoice is issued (payment confirmed), the system
creates workshop warranties automatically — no advisor step. Every task
always gets a "mano de obra" warranty (installs fail fast, so it runs on
the labor term); a task that also installed a part additionally gets a
"repuesto" warranty on its own, usually longer, term (a part wears out
slowly). Each is VIN-anchored, starts at the invoice's issuance date. A
vehicle with no VIN simply gets no warranties — the invoice still
succeeds."""

import uuid
from datetime import timedelta

import pytest
from billing_support import configure_billing, invoice_payload
from sqlalchemy import select
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_inventory

from app.modules.clients.models import Vehicle
from app.modules.parts.models import Part
from app.modules.post_ventas.enums import TemparioCategory, WorkshopWarrantyCoverage
from app.modules.post_ventas.models import Tempario, TemparioPart, WorkshopWarranty
from app.modules.post_ventas.service import PostVentasService
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrderTask
from app.modules.warehouse.models import PartLot

inventory = fifo_inventory
order_inventory = discount_inventory


@pytest.fixture
def ready(order_inventory):
    service, session, order, part_id, lots = order_inventory
    billing, accounts, settings = configure_billing(service, session, order)
    return service, session, order, part_id, lots, billing, accounts, settings


def add_two_tasks(session, order):
    session.add_all(
        [
            ServiceOrderTask(
                service_order_id=order.id, tempario_id=uuid.uuid4(),
                code_snapshot="MO-1", name_snapshot="Cambio de aceite", hours_snapshot=1,
            ),
            ServiceOrderTask(
                service_order_id=order.id, tempario_id=uuid.uuid4(),
                code_snapshot="MO-2", name_snapshot="Alineación", hours_snapshot=1,
            ),
        ]
    )


def workshop_warranties_for(session, order_id):
    return list(
        session.scalars(
            select(WorkshopWarranty).where(WorkshopWarranty.service_order_id == order_id)
        ).all()
    )


@pytest.mark.asyncio
async def test_issuing_invoice_creates_one_warranty_per_task(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    await service.add_transfer_line(order.id, part_id, 1)
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    order.technician_user_id = uuid.uuid4()
    order.intake_mileage = 15000
    add_two_tasks(session, order)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    invoice = await billing.issue(order.id, payload, None)

    warranties = workshop_warranties_for(session, order.id)
    assert len(warranties) == 2
    for w in warranties:
        assert w.vin == "1HGCM82633A123456"
        assert w.technician_user_id == order.technician_user_id
        assert w.coverage_type == WorkshopWarrantyCoverage.MANO_DE_OBRA
        assert w.starts_at == invoice.issued_at.date()
        assert w.duration_days == 90
        assert w.duration_km == 5000
        assert w.expires_at == w.starts_at + timedelta(days=90)
        assert w.expiration_mileage == 15000 + 5000
    assert {w.tempario_code_snapshot for w in warranties} == {"MO-1", "MO-2"}


@pytest.mark.asyncio
async def test_uses_configured_workshop_warranty_settings(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    await service.add_transfer_line(order.id, part_id, 1)
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    order.intake_mileage = 10000
    add_two_tasks(session, order)
    order.status = ServiceOrderStatus.COMPLETADO
    settings.workshop_warranty_days = 45
    settings.workshop_warranty_km = 3000
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    invoice = await billing.issue(order.id, payload, None)

    warranties = workshop_warranties_for(session, order.id)
    assert len(warranties) == 2
    for w in warranties:
        assert w.coverage_type == WorkshopWarrantyCoverage.MANO_DE_OBRA
        assert w.duration_days == 45
        assert w.duration_km == 3000
        assert w.expires_at == invoice.issued_at.date() + timedelta(days=45)
        assert w.expiration_mileage == 13000


@pytest.mark.asyncio
async def test_no_intake_mileage_leaves_expiration_mileage_null(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    await service.add_transfer_line(order.id, part_id, 1)
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    add_two_tasks(session, order)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    await billing.issue(order.id, payload, None)

    warranties = workshop_warranties_for(session, order.id)
    assert len(warranties) == 2
    assert all(w.expiration_mileage is None for w in warranties)


@pytest.mark.asyncio
async def test_vehicle_without_vin_invoices_normally_with_no_warranties(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    await service.add_transfer_line(order.id, part_id, 1)
    add_two_tasks(session, order)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    invoice = await billing.issue(order.id, payload, None)

    assert invoice.id is not None
    assert workshop_warranties_for(session, order.id) == []


@pytest.mark.asyncio
async def test_retrying_issue_does_not_duplicate_warranties(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    await service.add_transfer_line(order.id, part_id, 1)
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    add_two_tasks(session, order)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    first = await billing.issue(order.id, payload, None)
    second = await billing.issue(order.id, payload, None)

    assert first.id == second.id
    assert len(workshop_warranties_for(session, order.id)) == 2


@pytest.mark.asyncio
async def test_list_workshop_warranties_by_vin(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    await service.add_transfer_line(order.id, part_id, 1)
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    order.intake_mileage = 15000
    add_two_tasks(session, order)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    await billing.issue(order.id, payload, None)

    post_ventas = PostVentasService(service.db)
    reads = await post_ventas.list_workshop_warranties_by_vin(order.filial_id, vehicle.vin)

    assert len(reads) == 2
    assert all(r.status == "vigente" for r in reads)
    assert all(r.days_remaining == 90 for r in reads)


@pytest.mark.asyncio
async def test_task_with_installed_part_also_gets_repuesto_warranty(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    order.intake_mileage = 10000

    installed_part = Part(filial_id=order.filial_id, code="P-99", name="Bomba de agua", price=50, stock_quantity=0)
    tempario = Tempario(
        filial_id=order.filial_id, category=TemparioCategory.MOTOR, sequence_number=1,
        name="Cambio de bomba", estimated_hours=2,
    )
    session.add_all([installed_part, tempario])
    session.commit()
    session.add(
        TemparioPart(tempario_id=tempario.id, part_id=installed_part.id, name=installed_part.name, quantity=1, unit_cost=50)
    )
    session.add(
        PartLot(
            filial_id=order.filial_id, warehouse_id=lots[0].warehouse_id, part_id=installed_part.id,
            quantity_received=10, quantity_remaining=10, unit_cost=50,
        )
    )
    session.commit()

    await service.add_task(order.id, tempario.id)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    invoice = await billing.issue(order.id, payload, None)

    warranties = workshop_warranties_for(session, order.id)
    assert len(warranties) == 2
    by_coverage = {w.coverage_type: w for w in warranties}
    assert set(by_coverage) == {WorkshopWarrantyCoverage.MANO_DE_OBRA, WorkshopWarrantyCoverage.REPUESTO}
    for w in warranties:
        assert w.starts_at == invoice.issued_at.date()
        assert w.tempario_code_snapshot == tempario.code


@pytest.mark.asyncio
async def test_manual_part_not_linked_to_task_stays_labor_only(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    # Added directly to the order, not through any task.
    await service.add_transfer_line(order.id, part_id, 1)
    add_two_tasks(session, order)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    await billing.issue(order.id, payload, None)

    warranties = workshop_warranties_for(session, order.id)
    assert len(warranties) == 2
    assert all(w.coverage_type == WorkshopWarrantyCoverage.MANO_DE_OBRA for w in warranties)


@pytest.mark.asyncio
async def test_labor_and_parts_terms_can_differ(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    vehicle = session.get(Vehicle, order.vehicle_id)
    vehicle.vin = "1HGCM82633A123456"
    order.intake_mileage = 10000
    settings.workshop_warranty_days = 10
    settings.workshop_warranty_km = 1000
    settings.workshop_parts_warranty_days = 365
    settings.workshop_parts_warranty_km = 50000

    installed_part = Part(filial_id=order.filial_id, code="P-77", name="Correa", price=20, stock_quantity=0)
    tempario = Tempario(
        filial_id=order.filial_id, category=TemparioCategory.MOTOR, sequence_number=2,
        name="Cambio de correa", estimated_hours=1,
    )
    session.add_all([installed_part, tempario])
    session.commit()
    session.add(
        TemparioPart(tempario_id=tempario.id, part_id=installed_part.id, name=installed_part.name, quantity=1, unit_cost=20)
    )
    session.add(
        PartLot(
            filial_id=order.filial_id, warehouse_id=lots[0].warehouse_id, part_id=installed_part.id,
            quantity_received=10, quantity_remaining=10, unit_cost=20,
        )
    )
    session.commit()

    await service.add_task(order.id, tempario.id)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    invoice = await billing.issue(order.id, payload, None)
    starts_at = invoice.issued_at.date()

    warranties = workshop_warranties_for(session, order.id)
    by_coverage = {w.coverage_type: w for w in warranties}
    labor = by_coverage[WorkshopWarrantyCoverage.MANO_DE_OBRA]
    parts = by_coverage[WorkshopWarrantyCoverage.REPUESTO]

    assert labor.duration_days == 10 and labor.duration_km == 1000
    assert labor.expires_at == starts_at + timedelta(days=10)
    assert labor.expiration_mileage == 11000

    assert parts.duration_days == 365 and parts.duration_km == 50000
    assert parts.expires_at == starts_at + timedelta(days=365)
    assert parts.expiration_mileage == 60000
