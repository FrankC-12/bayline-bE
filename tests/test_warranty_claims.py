"""Intake screen for a customer's comeback complaint against the vehicle's
factory warranty. Search by plate/VIN, see vigente warranties with days/km
remaining, select which apply, record the reported symptom and mileage.
Creating a claim never opens a ServiceOrder — that's a manager-authorization
step left for future work. The mileage-inconsistency check is advisory only:
it flags, it never blocks."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import BadRequestError
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.inspections.models import PreliminaryInspection
from app.modules.post_ventas.enums import VehicleWarrantySource
from app.modules.post_ventas.models import VehicleWarranty
from app.modules.service_orders.enums import WarrantyClaimStatus
from app.modules.service_orders.exceptions import WarrantyClaimWarrantyMismatchError
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import WarrantyClaimCreate
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        filial_id = uuid.uuid4()
        session.add(Filial(id=filial_id, holding_id=uuid.uuid4(), name="Taller", slug="taller"))

        client = Client(
            filial_id=filial_id, full_name="Cliente Uno", client_type=ClientType.PARTICULAR,
            document_type=DocumentType.V, document_number="12345678", phone_primary="04121234567", address="Caracas",
        )
        session.add(client)
        session.commit()
        vehicle = Vehicle(
            client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123", vin="1HGCM82633A123456"
        )
        session.add(vehicle)
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, filial_id, vehicle


def make_warranty(session, filial_id, vin, expires_at, duration_km=None):
    warranty = VehicleWarranty(
        filial_id=filial_id, vin=vin, brand="Toyota", starts_at=date(2020, 1, 1),
        expires_at=expires_at, duration_km=duration_km, source=VehicleWarrantySource.MANUAL,
    )
    session.add(warranty)
    session.commit()
    return warranty


def make_inspection(session, filial_id, vehicle_id, mileage):
    inspection = PreliminaryInspection(
        filial_id=filial_id, vehicle_id=vehicle_id, inspector_user_id=uuid.uuid4(), mileage=mileage
    )
    session.add(inspection)
    session.commit()
    return inspection


@pytest.mark.asyncio
async def test_create_claim_with_vigente_warranty_stays_solicitado_no_order(env):
    service, session, filial_id, vehicle = env
    warranty = make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=365))

    result = await service.create_warranty_claim(
        WarrantyClaimCreate(
            vehicle_id=vehicle.id, warranty_ids=[warranty.id],
            reported_symptom="Ruido en el motor", reported_mileage=1000,
        ),
        None,
    )

    assert result.status == WarrantyClaimStatus.SOLICITADO
    assert result.warranty_ids == [warranty.id]
    assert session.query(ServiceOrder).count() == 0


@pytest.mark.asyncio
async def test_reported_mileage_below_current_flags_inconsistency_but_saves(env):
    service, session, filial_id, vehicle = env
    warranty = make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=365))
    make_inspection(session, filial_id, vehicle.id, mileage=5000)

    result = await service.create_warranty_claim(
        WarrantyClaimCreate(
            vehicle_id=vehicle.id, warranty_ids=[warranty.id],
            reported_symptom="No enciende", reported_mileage=3000,
        ),
        None,
    )

    assert result.vehicle_mileage_at_claim == 5000
    assert result.mileage_inconsistent is True
    assert result.status == WarrantyClaimStatus.SOLICITADO


@pytest.mark.asyncio
async def test_reported_mileage_at_or_above_current_is_consistent(env):
    service, session, filial_id, vehicle = env
    warranty = make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=365))
    make_inspection(session, filial_id, vehicle.id, mileage=5000)

    result = await service.create_warranty_claim(
        WarrantyClaimCreate(
            vehicle_id=vehicle.id, warranty_ids=[warranty.id],
            reported_symptom="No enciende", reported_mileage=5200,
        ),
        None,
    )

    assert result.mileage_inconsistent is False


@pytest.mark.asyncio
async def test_no_prior_mileage_on_record_skips_the_check(env):
    service, session, filial_id, vehicle = env
    warranty = make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=365))

    result = await service.create_warranty_claim(
        WarrantyClaimCreate(
            vehicle_id=vehicle.id, warranty_ids=[warranty.id],
            reported_symptom="No enciende", reported_mileage=10,
        ),
        None,
    )

    assert result.vehicle_mileage_at_claim is None
    assert result.mileage_inconsistent is False


@pytest.mark.asyncio
async def test_get_vehicle_warranties_for_claim_excludes_vencida(env):
    service, session, filial_id, vehicle = env
    make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() - timedelta(days=1))

    filial_returned, warranties = await service.get_vehicle_warranties_for_claim(vehicle.id)

    assert filial_returned == filial_id
    assert warranties == []


@pytest.mark.asyncio
async def test_get_vehicle_warranties_for_claim_returns_vigente_with_remaining(env):
    service, session, filial_id, vehicle = env
    vigente = make_warranty(
        session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=100), duration_km=80000
    )
    make_inspection(session, filial_id, vehicle.id, mileage=30000)

    filial_returned, warranties = await service.get_vehicle_warranties_for_claim(vehicle.id)

    assert filial_returned == filial_id
    assert len(warranties) == 1
    assert warranties[0].id == vigente.id
    assert warranties[0].status == "vigente"
    assert warranties[0].days_remaining == 100
    assert warranties[0].km_remaining == 50000


@pytest.mark.asyncio
async def test_warranty_from_another_vin_is_rejected(env):
    service, session, filial_id, vehicle = env
    other_warranty = make_warranty(
        session, filial_id, "OTHERVIN0000000AA", expires_at=date.today() + timedelta(days=100)
    )

    with pytest.raises(BadRequestError) as exc_info:
        await service.create_warranty_claim(
            WarrantyClaimCreate(
                vehicle_id=vehicle.id, warranty_ids=[other_warranty.id],
                reported_symptom="No enciende", reported_mileage=10,
            ),
            None,
        )
    assert isinstance(exc_info.value, WarrantyClaimWarrantyMismatchError)


def test_warranty_claim_create_requires_at_least_one_warranty():
    with pytest.raises(ValueError):
        WarrantyClaimCreate(
            vehicle_id=uuid.uuid4(), warranty_ids=[], reported_symptom="No enciende", reported_mileage=10
        )


@pytest.mark.asyncio
async def test_list_warranty_claims_returns_created_claims_most_recent_first(env):
    service, session, filial_id, vehicle = env
    warranty = make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=365))

    first = await service.create_warranty_claim(
        WarrantyClaimCreate(
            vehicle_id=vehicle.id, warranty_ids=[warranty.id],
            reported_symptom="Primer reclamo", reported_mileage=10,
        ),
        None,
    )
    # Force distinct timestamps — SQLite's CURRENT_TIMESTAMP has one-second
    # resolution, so two claims created back-to-back in a test can tie.
    from app.modules.service_orders.models import WarrantyClaim

    session.get(WarrantyClaim, first.id).created_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()

    second = await service.create_warranty_claim(
        WarrantyClaimCreate(
            vehicle_id=vehicle.id, warranty_ids=[warranty.id],
            reported_symptom="Segundo reclamo", reported_mileage=20,
        ),
        None,
    )

    claims = await service.list_warranty_claims(filial_id)
    assert [c.id for c in claims][:2] == [second.id, first.id]
    assert claims[0].vehicle_plate == "ABC123"
    assert claims[0].client_name == "Cliente Uno"
