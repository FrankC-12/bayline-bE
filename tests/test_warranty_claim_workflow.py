"""The unified warranty claim's status workflow: solicitado ->
autorizado/rechazado -> (only from autorizado) convertido_a_ods. Authorizing
never opens an order by itself — a separate "convertir a ODS" step does.
Rejecting a claim has no side effect (no automatic billable order, per
product decision)."""

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
from app.modules.post_ventas.enums import VehicleWarrantySource
from app.modules.post_ventas.models import VehicleWarranty
from app.modules.service_orders.enums import (
    ReworkFailureCategory,
    ServiceOrderPayer,
    ServiceOrderStatus,
    ServiceOrderType,
    WarrantyClaimStatus,
    WarrantyClaimType,
)
from app.modules.service_orders.exceptions import (
    FailureCategoryRequiredError,
    VehicleWarrantyRequiredError,
    WarrantyClaimAlreadyConvertedError,
    WarrantyClaimAlreadyDecidedError,
    WarrantyClaimNotAuthorizedError,
    WarrantyOverrideNoteRequiredError,
)
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import (
    WarrantyClaimAuthorizationInput,
    WarrantyClaimConvertInput,
    WarrantyClaimCreate,
)
from app.modules.service_orders.service import ServiceOrderService


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
        vehicle = Vehicle(client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123", vin="1HGCM82633A123456")
        session.add(vehicle)
        session.commit()
        yield ServiceOrderService(AsyncAdapter(session)), session, filial_id, vehicle


async def _create_comeback_claim(service, session, filial_id, vehicle):
    order = ServiceOrder(filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id)
    session.add(order)
    session.commit()
    from app.modules.service_orders.models import ServiceOrderInvoice

    session.add(
        ServiceOrderInvoice(
            service_order_id=order.id, request_id=uuid.uuid4(), request_hash="h", code=f"FAC-{order.id}",
            issued_at=datetime.now(UTC), total_usd=100, document={}, billed_client_id=vehicle.client_id,
            amount_paid_at_issuance=100,
        )
    )
    session.commit()
    claim = await service.create_warranty_claim(
        WarrantyClaimCreate(
            claim_type=WarrantyClaimType.COMEBACK, vehicle_id=vehicle.id, service_order_id=order.id,
            failure_cause="No enciende", reported_mileage=0,
        ),
        [], [], None,
    )
    return order, claim


@pytest.mark.asyncio
async def test_authorizing_a_comeback_does_not_open_an_order(env):
    service, session, filial_id, vehicle = env
    _order, claim = await _create_comeback_claim(service, session, filial_id, vehicle)

    authorized = await service.authorize_warranty_claim(
        claim.id,
        WarrantyClaimAuthorizationInput(decision="aprobado", failure_category=ReworkFailureCategory.MANO_DE_OBRA),
        None,
    )

    assert authorized.status == WarrantyClaimStatus.AUTORIZADO
    assert authorized.resulting_service_order_id is None
    assert session.query(ServiceOrder).count() == 1  # only the original order


@pytest.mark.asyncio
async def test_rejecting_opens_no_order(env):
    service, session, filial_id, vehicle = env
    _order, claim = await _create_comeback_claim(service, session, filial_id, vehicle)

    rejected = await service.authorize_warranty_claim(
        claim.id, WarrantyClaimAuthorizationInput(decision="rechazado"), None
    )

    assert rejected.status == WarrantyClaimStatus.RECHAZADO
    assert rejected.resulting_service_order_id is None
    assert session.query(ServiceOrder).count() == 1


@pytest.mark.asyncio
async def test_authorizing_a_comeback_without_failure_category_raises(env):
    service, session, filial_id, vehicle = env
    _order, claim = await _create_comeback_claim(service, session, filial_id, vehicle)

    with pytest.raises(FailureCategoryRequiredError):
        await service.authorize_warranty_claim(claim.id, WarrantyClaimAuthorizationInput(decision="aprobado"), None)


@pytest.mark.asyncio
async def test_convert_requires_authorized_status(env):
    service, session, filial_id, vehicle = env
    _order, claim = await _create_comeback_claim(service, session, filial_id, vehicle)

    with pytest.raises(WarrantyClaimNotAuthorizedError):
        await service.convert_warranty_claim_to_order(claim.id, WarrantyClaimConvertInput(), None)


@pytest.mark.asyncio
async def test_converting_twice_raises(env):
    service, session, filial_id, vehicle = env
    _order, claim = await _create_comeback_claim(service, session, filial_id, vehicle)
    await service.authorize_warranty_claim(
        claim.id,
        WarrantyClaimAuthorizationInput(decision="aprobado", failure_category=ReworkFailureCategory.MANO_DE_OBRA),
        None,
    )
    await service.convert_warranty_claim_to_order(claim.id, WarrantyClaimConvertInput(), None)

    with pytest.raises(WarrantyClaimAlreadyConvertedError):
        await service.convert_warranty_claim_to_order(claim.id, WarrantyClaimConvertInput(), None)


@pytest.mark.asyncio
async def test_authorizing_twice_raises(env):
    service, session, filial_id, vehicle = env
    _order, claim = await _create_comeback_claim(service, session, filial_id, vehicle)
    await service.authorize_warranty_claim(
        claim.id, WarrantyClaimAuthorizationInput(decision="rechazado"), None
    )

    with pytest.raises(WarrantyClaimAlreadyDecidedError):
        await service.authorize_warranty_claim(claim.id, WarrantyClaimAuthorizationInput(decision="rechazado"), None)


@pytest.mark.asyncio
async def test_converting_a_comeback_opens_retrabajo_order_with_taller_payer(env):
    service, session, filial_id, vehicle = env
    order, claim = await _create_comeback_claim(service, session, filial_id, vehicle)
    await service.authorize_warranty_claim(
        claim.id,
        WarrantyClaimAuthorizationInput(decision="aprobado", failure_category=ReworkFailureCategory.MANO_DE_OBRA),
        None,
    )

    converted = await service.convert_warranty_claim_to_order(claim.id, WarrantyClaimConvertInput(), None)

    assert converted.status == WarrantyClaimStatus.CONVERTIDO_A_ODS
    new_order = session.get(ServiceOrder, converted.resulting_service_order_id)
    assert new_order.order_type == ServiceOrderType.RETRABAJO
    assert new_order.vehicle_id == vehicle.id


@pytest.mark.asyncio
async def test_authorizing_fabrica_without_vigente_warranty_requires_override(env):
    service, session, filial_id, vehicle = env
    claim = await service.create_warranty_claim(
        WarrantyClaimCreate(
            claim_type=WarrantyClaimType.FABRICA, vehicle_id=vehicle.id,
            reported_symptom="Ruido", reported_mileage=0,
        ),
        [], [], None,
    )

    with pytest.raises(VehicleWarrantyRequiredError):
        await service.authorize_warranty_claim(claim.id, WarrantyClaimAuthorizationInput(decision="aprobado"), None)

    with pytest.raises(WarrantyOverrideNoteRequiredError):
        await service.authorize_warranty_claim(
            claim.id, WarrantyClaimAuthorizationInput(decision="aprobado", warranty_override=True), None
        )

    authorized = await service.authorize_warranty_claim(
        claim.id,
        WarrantyClaimAuthorizationInput(
            decision="aprobado", warranty_override=True, warranty_override_note="Cliente frecuente, se aprueba."
        ),
        None,
    )
    assert authorized.status == WarrantyClaimStatus.AUTORIZADO
    assert authorized.warranty_override is True


@pytest.mark.asyncio
async def test_authorizing_fabrica_with_vigente_warranty_needs_no_override(env):
    service, session, filial_id, vehicle = env
    session.add(VehicleWarranty(
        filial_id=filial_id, vin=vehicle.vin, brand="Toyota", starts_at=date(2020, 1, 1),
        expires_at=date.today() + timedelta(days=100), source=VehicleWarrantySource.MANUAL,
    ))
    session.commit()
    claim = await service.create_warranty_claim(
        WarrantyClaimCreate(
            claim_type=WarrantyClaimType.FABRICA, vehicle_id=vehicle.id,
            reported_symptom="Ruido", reported_mileage=0,
        ),
        [], [], None,
    )

    authorized = await service.authorize_warranty_claim(
        claim.id, WarrantyClaimAuthorizationInput(decision="aprobado"), None
    )

    assert authorized.status == WarrantyClaimStatus.AUTORIZADO
    assert authorized.warranty_override is False


@pytest.mark.asyncio
async def test_converting_fabrica_claim_with_no_prior_order_uses_reported_mileage(env):
    service, session, filial_id, vehicle = env
    session.add(VehicleWarranty(
        filial_id=filial_id, vin=vehicle.vin, brand="Toyota", starts_at=date(2020, 1, 1),
        expires_at=date.today() + timedelta(days=100), source=VehicleWarrantySource.MANUAL,
    ))
    session.commit()
    claim = await service.create_warranty_claim(
        WarrantyClaimCreate(
            claim_type=WarrantyClaimType.FABRICA, vehicle_id=vehicle.id,
            reported_symptom="Ruido", reported_mileage=12345,
        ),
        [], [], None,
    )
    await service.authorize_warranty_claim(claim.id, WarrantyClaimAuthorizationInput(decision="aprobado"), None)

    converted = await service.convert_warranty_claim_to_order(claim.id, WarrantyClaimConvertInput(), None)

    new_order = session.get(ServiceOrder, converted.resulting_service_order_id)
    assert new_order.order_type == ServiceOrderType.RETRABAJO
    assert new_order.intake_mileage == 12345
    assert new_order.vehicle_id == vehicle.id
