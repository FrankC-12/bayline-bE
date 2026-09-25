"""Selecting a warranty policy on an open ODS (one for labor, one for
parts, applying to the whole order) — validated against the policy's
applies_to/status, and locked once the order is invoiced just like every
other order field (require_editable_order)."""

import pytest
from billing_support import configure_billing, invoice_payload
from test_part_sales_fifo import inventory as fifo_inventory
from test_parts_discounts import order_inventory as discount_inventory

from app.core.exceptions import BadRequestError
from app.modules.post_ventas.enums import (
    WarrantyPolicyAppliesTo,
    WarrantyPolicyCoveredBy,
    WarrantyPolicyScope,
    WarrantyPolicyStatus,
)
from app.modules.post_ventas.models import WarrantyPolicy
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.exceptions import ServiceOrderReadOnlyError
from app.modules.service_orders.schemas import ServiceOrderUpdate

inventory = fifo_inventory
order_inventory = discount_inventory


def _policy(filial_id, **overrides):
    data = dict(
        filial_id=filial_id,
        name="Estándar de taller",
        applies_to=WarrantyPolicyAppliesTo.MANO_DE_OBRA,
        covered_by=WarrantyPolicyCoveredBy.LA_CASA,
        scope=WarrantyPolicyScope.PIEZA_MAS_INSTALACION,
        duration_days=90,
        duration_km=5000,
        status=WarrantyPolicyStatus.ACTIVA,
    )
    data.update(overrides)
    return WarrantyPolicy(**data)


@pytest.mark.asyncio
async def test_selecting_a_labor_policy_persists_it(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    policy = _policy(order.filial_id)
    session.add(policy)
    session.commit()

    updated = await service.update_order(order.id, ServiceOrderUpdate(labor_warranty_policy_id=policy.id))

    assert updated.labor_warranty_policy_id == policy.id


@pytest.mark.asyncio
async def test_rejects_a_labor_policy_that_only_applies_to_parts(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    policy = _policy(order.filial_id, applies_to=WarrantyPolicyAppliesTo.REPUESTOS)
    session.add(policy)
    session.commit()

    with pytest.raises(BadRequestError):
        await service.update_order(order.id, ServiceOrderUpdate(labor_warranty_policy_id=policy.id))


@pytest.mark.asyncio
async def test_rejects_an_inactive_policy(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    policy = _policy(order.filial_id, status=WarrantyPolicyStatus.INACTIVA)
    session.add(policy)
    session.commit()

    with pytest.raises(BadRequestError):
        await service.update_order(order.id, ServiceOrderUpdate(labor_warranty_policy_id=policy.id))


@pytest.mark.asyncio
async def test_ambas_policy_is_accepted_for_either_selector(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    policy = _policy(order.filial_id, applies_to=WarrantyPolicyAppliesTo.AMBAS)
    session.add(policy)
    session.commit()

    updated = await service.update_order(
        order.id,
        ServiceOrderUpdate(labor_warranty_policy_id=policy.id, parts_warranty_policy_id=policy.id),
    )

    assert updated.labor_warranty_policy_id == policy.id
    assert updated.parts_warranty_policy_id == policy.id


@pytest.mark.asyncio
async def test_clearing_an_already_selected_policy(order_inventory):
    service, session, order, _part_id, _lots = order_inventory
    policy = _policy(order.filial_id)
    session.add(policy)
    session.commit()
    await service.update_order(order.id, ServiceOrderUpdate(labor_warranty_policy_id=policy.id))

    updated = await service.update_order(order.id, ServiceOrderUpdate(clear_labor_warranty_policy=True))

    assert updated.labor_warranty_policy_id is None


@pytest.mark.asyncio
async def test_cannot_change_warranty_policy_once_invoiced(order_inventory):
    service, session, order, part_id, _lots = order_inventory
    billing, accounts, _settings = configure_billing(service, session, order)
    policy = _policy(order.filial_id)
    session.add(policy)
    session.commit()
    await service.add_transfer_line(order.id, part_id, 1)
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()

    payload = await invoice_payload(billing, order, accounts)
    await billing.issue(order.id, payload, None)

    with pytest.raises(ServiceOrderReadOnlyError):
        await service.update_order(order.id, ServiceOrderUpdate(labor_warranty_policy_id=policy.id))
