"""Rework claims (garantía de taller): a client's comeback complaint about an
already-invoiced ServiceOrder — distinct from VehicleWarranty (factory
coverage) and SupplierClaim (a claim against a parts supplier). Feeds the
Torre de Control rework-rate metric."""

import uuid
from datetime import date, timedelta

import pytest
from billing_support import configure_billing, invoice_payload
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_inventory

from app.core.exceptions import BadRequestError
from app.modules.post_ventas.enums import TemparioCategory
from app.modules.post_ventas.models import Tempario
from app.modules.service_orders.enums import ReworkFailureCategory, ServiceOrderStatus
from app.modules.service_orders.models import ServiceOrderTask
from app.modules.service_orders.schemas import ReworkClaimCreate
from app.modules.service_orders.service import ServiceOrderService

inventory = fifo_inventory
order_inventory = discount_inventory


@pytest.fixture
def ready(order_inventory):
    service, session, order, part_id, lots = order_inventory
    billing, accounts, settings = configure_billing(service, session, order)
    return service, session, order, part_id, lots, billing, accounts, settings


async def invoice_the_order(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready
    await service.add_transfer_line(order.id, part_id, 1)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()
    payload = await invoice_payload(billing, order, accounts)
    invoice = await billing.issue(order.id, payload, None)
    return invoice


@pytest.mark.asyncio
async def test_cannot_claim_against_an_order_that_is_not_invoiced(ready):
    service, session, order, part_id, lots, billing, accounts, settings = ready

    with pytest.raises(BadRequestError):
        await service.create_rework_claim(
            order.id,
            ReworkClaimCreate(failure_category=ReworkFailureCategory.NO_DETERMINADA, failure_cause="No enciende"),
            None,
        )


@pytest.mark.asyncio
async def test_create_and_list_rework_claim(ready):
    await invoice_the_order(ready)
    service, session, order, part_id, lots, *_ = ready

    tech_id = uuid.uuid4()
    claimed = date.today() + timedelta(days=5)
    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.MANO_DE_OBRA,
            failure_cause="Ruido en el motor tras el cambio de aceite",
            claimed_at=claimed,
        ),
        tech_id,
    )

    assert claim.service_order_id == order.id
    assert claim.failure_cause == "Ruido en el motor tras el cambio de aceite"
    assert claim.recorded_by_user_id == tech_id
    assert claim.days_since_invoice == 5

    listed = await service.list_rework_claims(order.id)
    assert len(listed) == 1
    assert listed[0].id == claim.id


@pytest.mark.asyncio
async def test_claim_can_reference_a_part_actually_consumed_on_the_order(ready):
    await invoice_the_order(ready)
    service, session, order, part_id, lots, *_ = ready

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.MANO_DE_OBRA,
            failure_cause="La pieza llegó defectuosa", part_id=part_id,
        ),
        None,
    )
    assert claim.part_id == part_id
    assert claim.part_name is not None


@pytest.mark.asyncio
async def test_claim_rejects_a_part_not_on_the_order(ready):
    await invoice_the_order(ready)
    service, session, order, part_id, lots, *_ = ready

    with pytest.raises(BadRequestError):
        await service.create_rework_claim(
            order.id,
            ReworkClaimCreate(
                failure_category=ReworkFailureCategory.MANO_DE_OBRA, failure_cause="xyz", part_id=uuid.uuid4()
            ),
            None,
        )


@pytest.mark.asyncio
async def test_claim_can_reference_a_tempario_actually_on_the_order(ready):
    invoice = await invoice_the_order(ready)
    service, session, order, part_id, lots, *_ = ready

    tempario = Tempario(
        filial_id=order.filial_id, category=TemparioCategory.MOTOR,
        sequence_number=1, name="Cambio de aceite", estimated_hours=1,
    )
    session.add(tempario)
    session.commit()
    session.add(
        ServiceOrderTask(
            service_order_id=order.id, tempario_id=tempario.id,
            code_snapshot="MT-1", name_snapshot=tempario.name, hours_snapshot=1,
        )
    )
    session.commit()

    claim = await service.create_rework_claim(
        order.id,
        ReworkClaimCreate(
            failure_category=ReworkFailureCategory.MANO_DE_OBRA,
            failure_cause="Fuga de aceite", tempario_id=tempario.id,
        ),
        None,
    )
    assert claim.tempario_id == tempario.id
    assert claim.tempario_name == "Cambio de aceite"

    with pytest.raises(BadRequestError):
        await service.create_rework_claim(
            order.id,
            ReworkClaimCreate(
                failure_category=ReworkFailureCategory.MANO_DE_OBRA, failure_cause="xyz", tempario_id=uuid.uuid4()
            ),
            None,
        )
