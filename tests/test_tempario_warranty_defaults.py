import uuid

import pytest
from billing_support import invoice_payload
from sqlalchemy import select
from test_tempario_pricing_matches_ods import env as tempario_env

from app.core.exceptions import BadRequestError
from app.modules.administracion.enums import AccountCurrency, AccountType
from app.modules.administracion.models import Account
from app.modules.clients.models import Vehicle
from app.modules.post_ventas.enums import (
    WarrantyPolicyAppliesTo,
    WarrantyPolicyCoveredBy,
    WarrantyPolicyScope,
    WarrantyPolicyStatus,
)
from app.modules.post_ventas.models import WarrantyPolicy, WorkshopWarranty
from app.modules.post_ventas.schemas import TemparioUpdate
from app.modules.service_orders.billing import BillingService
from app.modules.service_orders.enums import ServiceOrderStatus

warranty_env = tempario_env


def policy(db, filial_id, **kwargs):
    p = WarrantyPolicy(
        filial_id=filial_id,
        name="Servicio 90 días",
        applies_to=WarrantyPolicyAppliesTo.AMBAS,
        covered_by=WarrantyPolicyCoveredBy.LA_CASA,
        scope=WarrantyPolicyScope.PIEZA_MAS_INSTALACION,
        duration_days=90,
        duration_km=5000,
        **kwargs,
    )
    db.session.add(p)
    db.session.commit()
    return p


@pytest.mark.asyncio
async def test_edit_clear_and_reject_incompatible_or_foreign_guarantees(warranty_env):
    catalog, service, order, tempario = warranty_env
    p = policy(catalog.db, order.filial_id)
    read = await catalog.update_tempario(
        tempario.id, TemparioUpdate(labor_warranty_policy_id=p.id, parts_warranty_policy_id=p.id)
    )
    assert read.labor_warranty_policy_id == p.id
    p.status = WarrantyPolicyStatus.INACTIVA
    catalog.db.session.commit()
    await catalog.update_tempario(tempario.id, TemparioUpdate(name="Servicio actualizado"))
    await catalog.update_tempario(tempario.id, TemparioUpdate(labor_warranty_policy_id=p.id))
    await catalog.update_tempario(tempario.id, TemparioUpdate(labor_warranty_policy_id=None))
    with pytest.raises(BadRequestError, match="activa"):
        await catalog.update_tempario(tempario.id, TemparioUpdate(labor_warranty_policy_id=p.id))
    p.status = WarrantyPolicyStatus.ACTIVA
    p.applies_to = WarrantyPolicyAppliesTo.REPUESTOS
    catalog.db.session.commit()
    with pytest.raises(BadRequestError, match="cobertura"):
        await catalog.update_tempario(tempario.id, TemparioUpdate(labor_warranty_policy_id=p.id))
    foreign = policy(catalog.db, uuid.uuid4())
    with pytest.raises(BadRequestError, match="filial"):
        await catalog.update_tempario(
            tempario.id, TemparioUpdate(parts_warranty_policy_id=foreign.id)
        )


@pytest.mark.asyncio
async def test_task_freezes_guarantee_and_issuance_applies_original_terms(warranty_env):
    catalog, service, order, tempario = warranty_env
    db = catalog.db.session
    p = policy(catalog.db, order.filial_id)
    await catalog.update_tempario(
        tempario.id, TemparioUpdate(labor_warranty_policy_id=p.id, parts_warranty_policy_id=p.id)
    )
    task = await service.add_task(order.id, tempario.id)
    assert task.warranty_snapshot["labor"]["duration_days"] == 90
    p.duration_days = 7
    p.name = "Nuevo nombre"
    p.status = WarrantyPolicyStatus.INACTIVA
    db.get(Vehicle, order.vehicle_id).vin = "VIN-WARRANTY"
    db.commit()
    transfer = (await service.list_transfers(order.id))[0]
    await service.mark_transfer_ordered(transfer.id)
    await service.complete_transfer(transfer.id)
    order.status = ServiceOrderStatus.COMPLETADO
    accounts = [
        Account(
            filial_id=order.filial_id,
            name="Caja " + c.value,
            currency=c,
            account_type=AccountType.CAJA,
        )
        for c in (AccountCurrency.USD, AccountCurrency.BS)
    ]
    db.add_all(accounts)
    db.commit()
    billing = BillingService(service.db)
    invoice = await billing.issue(order.id, await invoice_payload(billing, order, accounts), None)
    assert invoice.document["warranties"][0]["duration_days"] == 90
    assert invoice.document["warranties"][0]["name"] == "Servicio 90 días"
    guarantees = db.scalars(select(WorkshopWarranty)).all()
    assert len(guarantees) == 2
    assert all(g.duration_days == 90 and g.duration_km == 5000 for g in guarantees)
    assert all(g.warranty_policy_name_snapshot == "Servicio 90 días" for g in guarantees)
    assert all(g.covered_by_snapshot == WarrantyPolicyCoveredBy.LA_CASA for g in guarantees)


@pytest.mark.asyncio
async def test_create_tempario_saves_selected_guarantees(warranty_env):
    from app.modules.post_ventas.enums import TemparioCategory
    from app.modules.post_ventas.schemas import TemparioCreate

    catalog, service, order, _ = warranty_env
    selected = policy(catalog.db, order.filial_id)
    created = await catalog.create_tempario(
        TemparioCreate(
            filial_id=order.filial_id,
            category=TemparioCategory.MOTOR,
            name="Revisión de motor",
            estimated_hours=1,
            labor_warranty_policy_id=selected.id,
            parts_warranty_policy_id=selected.id,
        )
    )
    assert created.labor_warranty_policy_id == selected.id
    assert created.parts_warranty_policy_id == selected.id
    await catalog.update_tempario(created.id, TemparioUpdate(parts_warranty_policy_id=None))
    assert (await catalog.get_tempario(created.id)).parts_warranty_policy_id is None


@pytest.mark.asyncio
async def test_foreign_tempario_cannot_be_added_to_an_order(warranty_env):
    catalog, service, order, tempario = warranty_env
    tempario.filial_id = uuid.uuid4()
    catalog.db.session.commit()
    with pytest.raises(BadRequestError, match="filial"):
        await service.add_task(order.id, tempario.id)
