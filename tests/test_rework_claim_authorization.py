"""An advisor can't give away warranty work on their own — a manager must
authorize a ReworkClaim before any redo work happens. Approving opens a real
new 'retrabajo' ServiceOrder inheriting the vehicle and the claim's flagged
work; rejecting opens a normal billable one instead. Approval requires a
vigente factory warranty on the vehicle, unless an explicit, recorded
exception is made."""

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
from app.modules.parts.models import Part
from app.modules.post_ventas.enums import TemparioCategory, VehicleWarrantySource
from app.modules.post_ventas.models import Tempario, VehicleWarranty
from app.modules.service_orders.enums import (
    ReworkAuthorizationStatus,
    ServiceOrderPayer,
    ServiceOrderType,
)
from app.modules.service_orders.models import ServiceOrder, ServiceOrderInvoice
from app.modules.service_orders.schemas import ReworkClaimAuthorizationInput, ReworkClaimCreate, ServiceOrderUpdate
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
        vehicle = Vehicle(
            client_id=client.id, brand="Toyota", model="Corolla", plate="ABC123", vin="1HGCM82633A123456"
        )
        session.add(vehicle)
        session.commit()

        order = ServiceOrder(
            filial_id=filial_id, sequence_number=1, vehicle_id=vehicle.id, advisor_user_id=uuid.uuid4(),
        )
        session.add(order)
        session.commit()
        session.add(
            ServiceOrderInvoice(
                service_order_id=order.id, request_id=uuid.uuid4(), request_hash="h", code=f"FAC-{order.id}",
                issued_at=datetime.now(UTC), total_usd=100, document={}, billed_client_id=client.id,
                amount_paid_at_issuance=100,
            )
        )
        session.commit()

        db = AsyncAdapter(session)
        yield ServiceOrderService(db), session, filial_id, order, vehicle


def make_warranty(session, filial_id, vin, expires_at):
    warranty = VehicleWarranty(
        filial_id=filial_id, vin=vin, brand="Toyota", starts_at=date(2020, 1, 1),
        expires_at=expires_at, source=VehicleWarrantySource.MANUAL,
    )
    session.add(warranty)
    session.commit()
    return warranty


@pytest.mark.asyncio
async def test_approve_with_vigente_warranty_opens_a_retrabajo_order(env):
    service, session, filial_id, order, vehicle = env
    make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() + timedelta(days=365))

    tempario = Tempario(
        filial_id=filial_id, category=TemparioCategory.MOTOR, sequence_number=1,
        name="Cambio de aceite", estimated_hours=1,
    )
    part = Part(category_id=uuid.uuid4(), filial_id=filial_id, code="P-1", name="Filtro", price=10, stock_quantity=0)
    session.add_all([tempario, part])
    session.commit()
    await service.add_task(order.id, tempario.id)

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(failure_cause="Fuga de aceite", tempario_id=tempario.id, part_id=None),
        None,
    )

    approver = uuid.uuid4()
    result = await service.authorize_rework_claim(
        claim.id, ReworkClaimAuthorizationInput(decision="aprobado"), approver
    )

    assert result.authorization_status == ReworkAuthorizationStatus.APROBADO
    assert result.authorized_by_user_id == approver
    assert result.authorized_at is not None
    assert result.resulting_service_order_id is not None

    new_order = session.get(ServiceOrder, result.resulting_service_order_id)
    assert new_order.order_type == ServiceOrderType.RETRABAJO
    assert new_order.vehicle_id == order.vehicle_id
    assert new_order.advisor_user_id == order.advisor_user_id

    inherited_tasks = await service.list_tasks(new_order.id)
    assert inherited_tasks[0].payer == ServiceOrderPayer.GARANTIA_TALLER


@pytest.mark.asyncio
async def test_approve_without_warranty_and_no_override_raises(env):
    service, _session, _filial_id, order, _vehicle = env
    claim = await service.create_rework_claim(
        order.id, ReworkClaimCreate(failure_cause="No enciende"), None
    )

    with pytest.raises(BadRequestError):
        await service.authorize_rework_claim(claim.id, ReworkClaimAuthorizationInput(decision="aprobado"), None)


@pytest.mark.asyncio
async def test_approve_without_warranty_override_without_note_raises(env):
    service, _session, _filial_id, order, _vehicle = env
    claim = await service.create_rework_claim(
        order.id, ReworkClaimCreate(failure_cause="No enciende"), None
    )

    with pytest.raises(BadRequestError):
        await service.authorize_rework_claim(
            claim.id, ReworkClaimAuthorizationInput(decision="aprobado", warranty_override=True), None
        )


@pytest.mark.asyncio
async def test_approve_without_warranty_override_with_note_succeeds(env):
    service, session, _filial_id, order, _vehicle = env
    claim = await service.create_rework_claim(
        order.id, ReworkClaimCreate(failure_cause="No enciende"), None
    )

    result = await service.authorize_rework_claim(
        claim.id,
        ReworkClaimAuthorizationInput(
            decision="aprobado", warranty_override=True, warranty_override_note="Cliente frecuente, cortesía."
        ),
        None,
    )

    assert result.authorization_status == ReworkAuthorizationStatus.APROBADO
    assert result.warranty_override is True
    assert result.warranty_override_note == "Cliente frecuente, cortesía."
    new_order = session.get(ServiceOrder, result.resulting_service_order_id)
    assert new_order.order_type == ServiceOrderType.RETRABAJO


@pytest.mark.asyncio
async def test_expired_warranty_also_blocks_approval(env):
    service, session, filial_id, order, vehicle = env
    make_warranty(session, filial_id, vehicle.vin, expires_at=date.today() - timedelta(days=1))
    claim = await service.create_rework_claim(
        order.id, ReworkClaimCreate(failure_cause="No enciende"), None
    )

    with pytest.raises(BadRequestError):
        await service.authorize_rework_claim(claim.id, ReworkClaimAuthorizationInput(decision="aprobado"), None)


@pytest.mark.asyncio
async def test_reject_opens_a_regular_order_no_warranty_check(env):
    service, session, _filial_id, order, _vehicle = env
    claim = await service.create_rework_claim(
        order.id, ReworkClaimCreate(failure_cause="Reclamo sin mérito"), None
    )

    result = await service.authorize_rework_claim(
        claim.id, ReworkClaimAuthorizationInput(decision="rechazado"), None
    )

    assert result.authorization_status == ReworkAuthorizationStatus.RECHAZADO
    new_order = session.get(ServiceOrder, result.resulting_service_order_id)
    assert new_order.order_type == ServiceOrderType.REGULAR
    assert new_order.vehicle_id == order.vehicle_id


@pytest.mark.asyncio
async def test_cannot_authorize_a_claim_twice(env):
    service, _session, _filial_id, order, _vehicle = env
    claim = await service.create_rework_claim(
        order.id, ReworkClaimCreate(failure_cause="Reclamo"), None
    )
    await service.authorize_rework_claim(claim.id, ReworkClaimAuthorizationInput(decision="rechazado"), None)

    with pytest.raises(BadRequestError):
        await service.authorize_rework_claim(claim.id, ReworkClaimAuthorizationInput(decision="aprobado"), None)


def test_service_order_update_has_no_vehicle_field():
    """Documents the existing structural guarantee this feature relies on:
    a ServiceOrder's vehicle can never be changed after creation."""
    assert "vehicle_id" not in ServiceOrderUpdate.model_fields
