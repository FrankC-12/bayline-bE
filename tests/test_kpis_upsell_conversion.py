"""Upsell conversion-rate KPI: of the upsells postponed within a period, how
many eventually became a real ODS task — independent of whatever their
CURRENT status is, since was_postponed/postponed_at are set once and never
overwritten by a later decision (unlike `status`)."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter, make_order_type

from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.kpis.service import KpiService
from app.modules.service_orders.enums import UpsellSeverity, UpsellStatus
from app.modules.service_orders.models import ServiceOrder, Upsell


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
            filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id,
            order_type_id=make_order_type(session, filial_id),
        )
        session.add(order)
        session.commit()

        db = AsyncAdapter(session)
        yield KpiService(db), session, filial_id, order


def _upsell(session, order, *, status, was_postponed, postponed_at=None, title="U"):
    upsell = Upsell(
        service_order_id=order.id, title=title, description="Desc", severity=UpsellSeverity.MONITOREAR,
        status=status, was_postponed=was_postponed, postponed_at=postponed_at,
    )
    session.add(upsell)
    session.commit()
    return upsell


@pytest.mark.asyncio
async def test_rate_counts_converted_over_postponed_in_range(env):
    service, session, filial_id, order = env
    in_range = date(2026, 3, 15)
    # Postponed in March, later converted (status moved on to aprobado).
    _upsell(session, order, status=UpsellStatus.APROBADO, was_postponed=True, postponed_at=datetime(2026, 3, 10, tzinfo=UTC))
    # Postponed in March, still postponed — never converted.
    _upsell(session, order, status=UpsellStatus.POSPUESTO, was_postponed=True, postponed_at=datetime(2026, 3, 20, tzinfo=UTC))
    # Postponed in March, eventually discarded — never converted.
    _upsell(session, order, status=UpsellStatus.RECHAZADO, was_postponed=True, postponed_at=datetime(2026, 3, 25, tzinfo=UTC))
    # Never postponed at all (same-visit approval) — excluded regardless of status.
    _upsell(session, order, status=UpsellStatus.APROBADO, was_postponed=False)
    # Postponed OUTSIDE the queried range — excluded.
    _upsell(session, order, status=UpsellStatus.APROBADO, was_postponed=True, postponed_at=datetime(2026, 4, 5, tzinfo=UTC))

    report = await service.get_upsell_conversion_rate(filial_id, date(2026, 3, 1), date(2026, 3, 31))

    assert report.postponed_count == 3
    assert report.converted_count == 1
    assert report.rate == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_rate_is_zero_with_no_postponed_upsells(env):
    service, _session, filial_id, _order = env
    report = await service.get_upsell_conversion_rate(filial_id, date(2026, 1, 1), date(2026, 1, 31))
    assert report.postponed_count == 0
    assert report.converted_count == 0
    assert report.rate == 0.0


@pytest.mark.asyncio
async def test_rate_scoped_to_filial(env):
    service, session, filial_id, order = env
    other_filial_id = uuid.uuid4()
    session.add(Filial(id=other_filial_id, holding_id=uuid.uuid4(), name="Otro taller", slug="otro-taller"))
    other_client = Client(
        filial_id=other_filial_id, full_name="Otro cliente", client_type=ClientType.PARTICULAR,
        document_type=DocumentType.V, document_number="87654321", phone_primary="04121234567", address="Caracas",
    )
    session.add(other_client)
    session.commit()
    other_vehicle = Vehicle(client_id=other_client.id, brand="Ford", model="Fiesta", plate="XYZ999")
    session.add(other_vehicle)
    session.commit()
    other_order = ServiceOrder(
        filial_id=other_filial_id, sequence_number=1, vehicle_id=other_vehicle.id,
        order_type_id=make_order_type(session, other_filial_id),
    )
    session.add(other_order)
    session.commit()

    _upsell(session, other_order, status=UpsellStatus.APROBADO, was_postponed=True, postponed_at=datetime(2026, 3, 10, tzinfo=UTC))

    report = await service.get_upsell_conversion_rate(filial_id, date(2026, 3, 1), date(2026, 3, 31))
    assert report.postponed_count == 0
