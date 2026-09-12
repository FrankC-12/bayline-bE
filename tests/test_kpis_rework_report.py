"""Torre de Control rework-rate report: of the orders invoiced in a period,
how many came back with a rework claim, broken down by técnico, servicio
and repuesto, plus the days-to-claim split (days ~ workmanship, months ~
normal wear)."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.kpis.service import KpiService
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import Tempario
from app.modules.parts.models import Part
from app.modules.service_orders.enums import ReworkFailureCategory
from app.modules.service_orders.models import ReworkClaim, ServiceOrder, ServiceOrderInvoice


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
        db = AsyncAdapter(session)
        yield KpiService(db), session, filial_id, client.id


def make_vehicle(session, client_id):
    vehicle = Vehicle(client_id=client_id, brand="Toyota", model="Corolla", plate="ABC123")
    session.add(vehicle)
    session.commit()
    return vehicle


def make_invoiced_order(session, filial_id, client_id, technician_user_id, issued_at):
    vehicle = make_vehicle(session, client_id)
    order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id, technician_user_id=technician_user_id)
    session.add(order)
    session.commit()
    invoice = ServiceOrderInvoice(
        service_order_id=order.id, request_id=uuid.uuid4(), request_hash="h", code=f"FAC-{order.id}",
        issued_at=issued_at, total_usd=100, document={}, billed_client_id=client_id, amount_paid_at_issuance=100,
    )
    session.add(invoice)
    session.commit()
    return order, invoice


@pytest.mark.asyncio
async def test_rework_rate_and_breakdown(env):
    service, session, filial_id, client_id = env
    tech_a = uuid.uuid4()
    tech_b = uuid.uuid4()
    now = datetime(2026, 3, 15, tzinfo=timezone.utc)

    order1, _ = make_invoiced_order(session, filial_id, client_id, tech_a, now)
    order2, _ = make_invoiced_order(session, filial_id, client_id, tech_a, now)
    order3, _ = make_invoiced_order(session, filial_id, client_id, tech_b, now)

    tempario = Tempario(filial_id=filial_id, category=TemparioCategory.MOTOR, sequence_number=1, name="Cambio de aceite", estimated_hours=1)
    part = Part(filial_id=filial_id, code="P-1", name="Filtro de aceite", price=10)
    session.add_all([tempario, part])
    session.commit()

    # Quick claim (5 days) on order1/tech_a — likely workmanship.
    session.add(ReworkClaim(
        filial_id=filial_id, service_order_id=order1.id, tempario_id=tempario.id,
        failure_category=ReworkFailureCategory.MANO_DE_OBRA,
        failure_cause="Fuga de aceite", claimed_at=date(2026, 3, 20),
    ))
    # Slow claim (60 days) on order3/tech_b, referencing a part — likely normal wear.
    session.add(ReworkClaim(
        filial_id=filial_id, service_order_id=order3.id, part_id=part.id,
        failure_category=ReworkFailureCategory.NO_DETERMINADA,
        failure_cause="Filtro obstruido", claimed_at=date(2026, 5, 14),
    ))
    session.commit()

    report = await service.get_rework_report(filial_id, date(2026, 3, 1), date(2026, 3, 31))

    assert report.invoiced_orders_count == 3
    assert report.orders_with_claim_count == 2
    assert report.rework_rate == pytest.approx(2 / 3)
    assert report.avg_days_to_claim == pytest.approx((5 + 60) / 2)
    assert report.quick_claims_count == 1
    assert report.slow_claims_count == 1

    by_tech = {row.user_id: row for row in report.by_technician}
    assert by_tech[tech_a].invoiced_orders_count == 2
    assert by_tech[tech_a].claims_count == 1
    assert by_tech[tech_a].rework_rate == pytest.approx(0.5)
    assert by_tech[tech_a].avg_days_to_claim == pytest.approx(5)
    assert by_tech[tech_b].invoiced_orders_count == 1
    assert by_tech[tech_b].rework_rate == pytest.approx(1.0)

    assert len(report.by_service) == 1
    assert report.by_service[0].tempario_name == "Cambio de aceite"
    assert report.by_service[0].claims_count == 1

    assert len(report.by_part) == 1
    assert report.by_part[0].part_name == "Filtro de aceite"
    assert report.by_part[0].claims_count == 1

    # order2 (tech_a, no claim) must not appear in either breakdown's claim counts,
    # and orders outside the invoicing window must not count at all.
    order4, _ = make_invoiced_order(session, filial_id, client_id, tech_a, datetime(2026, 4, 15, tzinfo=timezone.utc))
    session.add(ReworkClaim(
        filial_id=filial_id, service_order_id=order4.id, failure_category=ReworkFailureCategory.NO_DETERMINADA,
        failure_cause="Fuera de rango", claimed_at=date(2026, 4, 20),
    ))
    session.commit()
    report_march = await service.get_rework_report(filial_id, date(2026, 3, 1), date(2026, 3, 31))
    assert report_march.invoiced_orders_count == 3  # order4 was invoiced in April, excluded


@pytest.mark.asyncio
async def test_no_invoiced_orders_returns_a_clean_zero_report(env):
    service, session, filial_id, client_id = env
    report = await service.get_rework_report(filial_id, date(2026, 1, 1), date(2026, 1, 31))
    assert report.invoiced_orders_count == 0
    assert report.orders_with_claim_count == 0
    assert report.rework_rate == 0
    assert report.avg_days_to_claim is None
    assert report.by_technician == []
