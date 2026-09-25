"""garantia_fabrica/comeback/campana are order types an advisor picks by hand
(unlike retrabajo, opened automatically by convert_warranty_claim_to_order) —
each requires linking an existing, AUTORIZADO WarrantyClaim for the same
vehicle, of the matching claim_type. This is a second, independent path to a
claim-linked order alongside the "Convertir a ODS" flow on the Reclamos screen."""

import os
import uuid
from datetime import date, datetime

os.environ["DEBUG"] = "false"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.modules.inspections.models import PreliminaryInspection
from app.modules.service_orders.enums import ServiceOrderType, WarrantyClaimStatus, WarrantyClaimType
from app.modules.service_orders.exceptions import (
    WarrantyClaimNotFoundError,
    WarrantyClaimOrderMismatchError,
    WarrantyClaimRequiredForOrderTypeError,
)
from app.modules.service_orders.models import WarrantyClaim
from app.modules.service_orders.schemas import ServiceOrderCreate
from app.modules.service_orders.service import ServiceOrderService


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        yield ServiceOrderService(AsyncAdapter(session)), session


def _make_inspection(session, vehicle_id, mileage=15000):
    inspection = PreliminaryInspection(
        filial_id=uuid.uuid4(), vehicle_id=vehicle_id, inspector_user_id=uuid.uuid4(),
        mileage=mileage, notes="Motivo de la visita",
    )
    session.add(inspection)
    session.flush()
    return inspection


def _make_claim(session, filial_id, vehicle_id, claim_type, status=WarrantyClaimStatus.AUTORIZADO):
    claim = WarrantyClaim(
        filial_id=filial_id, sequence_number=1, claim_type=claim_type, vehicle_id=vehicle_id,
        reported_mileage=10000, claimed_at=date(2026, 9, 1), status=status,
    )
    session.add(claim)
    session.flush()
    return claim


def _payload(filial_id, vehicle_id, advisor_id, inspection_id, **overrides):
    fields = {
        "filial_id": filial_id,
        "vehicle_id": vehicle_id,
        "customer_reason": "Ruido en frenos delanteros",
        "advisor_user_id": advisor_id,
        "promised_at": datetime(2026, 9, 10, 9, 0),
        "inspection_id": inspection_id,
    }
    fields.update(overrides)
    return ServiceOrderCreate(**fields)


@pytest.mark.asyncio
async def test_garantia_fabrica_order_requires_a_claim(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id)

    with pytest.raises(WarrantyClaimRequiredForOrderTypeError):
        await service.create_order(
            _payload(
                filial_id, vehicle_id, advisor_id, inspection.id,
                order_type=ServiceOrderType.GARANTIA_FABRICA,
            )
        )


@pytest.mark.asyncio
async def test_claim_for_a_different_vehicle_is_rejected(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id)
    claim = _make_claim(session, filial_id, uuid.uuid4(), WarrantyClaimType.FABRICA)

    with pytest.raises(WarrantyClaimOrderMismatchError):
        await service.create_order(
            _payload(
                filial_id, vehicle_id, advisor_id, inspection.id,
                order_type=ServiceOrderType.GARANTIA_FABRICA, warranty_claim_id=claim.id,
            )
        )


@pytest.mark.asyncio
async def test_claim_of_the_wrong_type_is_rejected(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id)
    claim = _make_claim(session, filial_id, vehicle_id, WarrantyClaimType.COMEBACK)

    with pytest.raises(WarrantyClaimOrderMismatchError):
        await service.create_order(
            _payload(
                filial_id, vehicle_id, advisor_id, inspection.id,
                order_type=ServiceOrderType.GARANTIA_FABRICA, warranty_claim_id=claim.id,
            )
        )


@pytest.mark.asyncio
async def test_an_unauthorized_claim_is_rejected(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id)
    claim = _make_claim(
        session, filial_id, vehicle_id, WarrantyClaimType.FABRICA, status=WarrantyClaimStatus.SOLICITADO
    )

    with pytest.raises(WarrantyClaimOrderMismatchError):
        await service.create_order(
            _payload(
                filial_id, vehicle_id, advisor_id, inspection.id,
                order_type=ServiceOrderType.GARANTIA_FABRICA, warranty_claim_id=claim.id,
            )
        )


@pytest.mark.asyncio
async def test_an_unknown_claim_id_raises_not_found(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id)

    with pytest.raises(WarrantyClaimNotFoundError):
        await service.create_order(
            _payload(
                filial_id, vehicle_id, advisor_id, inspection.id,
                order_type=ServiceOrderType.GARANTIA_FABRICA, warranty_claim_id=uuid.uuid4(),
            )
        )


@pytest.mark.asyncio
async def test_a_matching_authorized_claim_links_successfully(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id)
    claim = _make_claim(session, filial_id, vehicle_id, WarrantyClaimType.FABRICA)

    order = await service.create_order(
        _payload(
            filial_id, vehicle_id, advisor_id, inspection.id,
            order_type=ServiceOrderType.GARANTIA_FABRICA, warranty_claim_id=claim.id,
        )
    )

    assert order.order_type == ServiceOrderType.GARANTIA_FABRICA
    assert order.warranty_claim_id == claim.id


@pytest.mark.asyncio
async def test_comeback_and_campana_also_require_the_matching_claim_type(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    comeback_inspection = _make_inspection(session, vehicle_id)
    comeback_claim = _make_claim(session, filial_id, vehicle_id, WarrantyClaimType.COMEBACK)
    comeback_order = await service.create_order(
        _payload(
            filial_id, vehicle_id, advisor_id, comeback_inspection.id,
            order_type=ServiceOrderType.COMEBACK, warranty_claim_id=comeback_claim.id,
        )
    )
    assert comeback_order.warranty_claim_id == comeback_claim.id

    campana_inspection = _make_inspection(session, vehicle_id)
    campana_claim = _make_claim(session, filial_id, vehicle_id, WarrantyClaimType.CAMPANA_RECALL)
    campana_order = await service.create_order(
        _payload(
            filial_id, vehicle_id, advisor_id, campana_inspection.id,
            order_type=ServiceOrderType.CAMPANA, warranty_claim_id=campana_claim.id,
        )
    )
    assert campana_order.warranty_claim_id == campana_claim.id


@pytest.mark.asyncio
async def test_a_stray_claim_id_is_ignored_for_a_regular_order(env):
    service, session = env
    filial_id, vehicle_id, advisor_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    inspection = _make_inspection(session, vehicle_id)
    claim = _make_claim(session, filial_id, vehicle_id, WarrantyClaimType.FABRICA)

    order = await service.create_order(
        _payload(
            filial_id, vehicle_id, advisor_id, inspection.id,
            order_type=ServiceOrderType.REGULAR, warranty_claim_id=claim.id,
        )
    )

    assert order.warranty_claim_id is None
