"""Unified "reclamo de garantía" — covers factory/importer, comeback
(garantía taller), defective-part (proveedor), and campaign/recall claims.
Creating a claim never opens a ServiceOrder; a separate two-step
authorize -> convert-to-order does. Mileage is inherited from the vehicle's
last known reading (advisory-only inconsistency check, never blocks)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.clients.enums import ClientType, DocumentType
from app.modules.clients.models import Client, Vehicle
from app.modules.filiales.models import Filial
from app.modules.inspections.models import PreliminaryInspection
from app.modules.post_ventas.enums import VehicleWarrantySource
from app.modules.post_ventas.models import VehicleWarranty
from app.modules.service_orders.enums import WarrantyClaimStatus, WarrantyClaimType
from app.modules.service_orders.models import ServiceOrder, WarrantyClaim
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


def fabrica_payload(vehicle_id, **overrides):
    defaults = dict(
        claim_type=WarrantyClaimType.FABRICA,
        vehicle_id=vehicle_id,
        reported_symptom="Ruido en el motor",
        reported_mileage=1000,
    )
    defaults.update(overrides)
    return WarrantyClaimCreate(**defaults)


@pytest.mark.asyncio
async def test_create_fabrica_claim_stays_solicitado_no_order(env):
    service, session, filial_id, vehicle = env

    result = await service.create_warranty_claim(fabrica_payload(vehicle.id), [], [], None)

    assert result.status == WarrantyClaimStatus.SOLICITADO
    assert result.claim_type == WarrantyClaimType.FABRICA
    assert result.code.startswith("RG-")
    assert session.query(ServiceOrder).count() == 0


@pytest.mark.asyncio
async def test_reported_mileage_below_current_flags_inconsistency_but_saves(env):
    service, session, filial_id, vehicle = env
    make_inspection(session, filial_id, vehicle.id, mileage=5000)

    result = await service.create_warranty_claim(
        fabrica_payload(vehicle.id, reported_mileage=3000), [], [], None
    )

    assert result.vehicle_mileage_at_claim == 5000
    assert result.mileage_inconsistent is True
    assert result.status == WarrantyClaimStatus.SOLICITADO


@pytest.mark.asyncio
async def test_reported_mileage_at_or_above_current_is_consistent(env):
    service, session, filial_id, vehicle = env
    make_inspection(session, filial_id, vehicle.id, mileage=5000)

    result = await service.create_warranty_claim(
        fabrica_payload(vehicle.id, reported_mileage=5200), [], [], None
    )

    assert result.mileage_inconsistent is False


@pytest.mark.asyncio
async def test_no_prior_mileage_on_record_skips_the_check(env):
    service, session, filial_id, vehicle = env

    result = await service.create_warranty_claim(
        fabrica_payload(vehicle.id, reported_mileage=10), [], [], None
    )

    assert result.vehicle_mileage_at_claim is None
    assert result.mileage_inconsistent is False


@pytest.mark.asyncio
async def test_context_reports_vencida_factory_warranty(env):
    service, session, filial_id, vehicle = env
    make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() - timedelta(days=1))

    context = await service.get_claim_context(vehicle.id)

    assert context.factory_warranty is not None
    assert context.factory_warranty.status == "vencida"


@pytest.mark.asyncio
async def test_context_reports_vigente_factory_warranty_with_remaining(env):
    service, session, filial_id, vehicle = env
    vigente = make_warranty(
        session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=100), duration_km=80000
    )
    make_inspection(session, filial_id, vehicle.id, mileage=30000)

    context = await service.get_claim_context(vehicle.id)

    assert context.factory_warranty.id == vigente.id
    assert context.factory_warranty.status == "vigente"
    assert context.current_mileage == 30000


@pytest.mark.asyncio
async def test_context_reports_no_factory_warranty_when_none_exists(env):
    service, session, filial_id, vehicle = env

    context = await service.get_claim_context(vehicle.id)

    assert context.factory_warranty is None


@pytest.mark.asyncio
async def test_comeback_without_service_order_is_rejected():
    with pytest.raises(ValueError):
        WarrantyClaimCreate(
            claim_type=WarrantyClaimType.COMEBACK,
            vehicle_id=uuid.uuid4(),
            failure_cause="No enciende",
            reported_mileage=10,
        )


@pytest.mark.asyncio
async def test_fabrica_claim_without_symptom_is_rejected():
    with pytest.raises(ValueError):
        WarrantyClaimCreate(
            claim_type=WarrantyClaimType.FABRICA,
            vehicle_id=uuid.uuid4(),
            reported_mileage=10,
        )


@pytest.mark.asyncio
async def test_duplicate_open_claim_for_same_vehicle_and_part_is_surfaced(env):
    service, session, filial_id, vehicle = env
    part_id = uuid.uuid4()

    first = await service.create_warranty_claim(
        fabrica_payload(vehicle.id, part_id=part_id), [], [], None
    )

    context = await service.get_claim_context(vehicle.id, part_id=part_id)

    assert context.duplicate_open_claim is not None
    assert context.duplicate_open_claim.id == first.id


@pytest.mark.asyncio
async def test_no_duplicate_warning_when_no_component_given(env):
    service, session, filial_id, vehicle = env
    await service.create_warranty_claim(fabrica_payload(vehicle.id), [], [], None)

    context = await service.get_claim_context(vehicle.id)

    assert context.duplicate_open_claim is None


@pytest.mark.asyncio
async def test_list_warranty_claims_returns_created_claims_most_recent_first(env):
    service, session, filial_id, vehicle = env

    first = await service.create_warranty_claim(
        fabrica_payload(vehicle.id, reported_symptom="Primer reclamo", reported_mileage=10), [], [], None
    )
    # Force distinct timestamps — SQLite's CURRENT_TIMESTAMP has one-second
    # resolution, so two claims created back-to-back in a test can tie.
    session.get(WarrantyClaim, first.id).created_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()

    second = await service.create_warranty_claim(
        fabrica_payload(vehicle.id, reported_symptom="Segundo reclamo", reported_mileage=20), [], [], None
    )

    claims = await service.list_warranty_claims(filial_id)
    assert [c.id for c in claims][:2] == [second.id, first.id]
    assert claims[0].vehicle_plate == "ABC123"
    assert claims[0].client_name == "Cliente Uno"


@pytest.mark.asyncio
async def test_sequence_numbers_increment_per_filial(env):
    service, session, filial_id, vehicle = env

    first = await service.create_warranty_claim(fabrica_payload(vehicle.id), [], [], None)
    second = await service.create_warranty_claim(fabrica_payload(vehicle.id), [], [], None)

    assert second.code == f"RG-{int(first.code.split('-')[1]) + 1}"


@pytest.mark.asyncio
async def test_photo_and_document_urls_are_persisted(env):
    service, session, filial_id, vehicle = env

    result = await service.create_warranty_claim(
        fabrica_payload(vehicle.id), ["/uploads/warranty-claims/a.jpg"], ["/uploads/warranty-claims/b.pdf"], None
    )

    assert result.photo_urls == ["/uploads/warranty-claims/a.jpg"]
    assert result.document_urls == ["/uploads/warranty-claims/b.pdf"]
