"""Web drafts remain separate from mobile reception until explicitly submitted."""

import uuid
from types import SimpleNamespace

import pytest
import test_pending_upsells_cross_visit as visit_fixtures
from pydantic import ValidationError
from sqlalchemy import select

from app.core.exceptions import ConflictError
from app.modules.post_ventas.models import WorkshopWarranty
from app.modules.service_orders.claims_workflow import ClaimsWorkflow
from app.modules.service_orders.drafts import DraftService
from app.modules.service_orders.enums import ServiceOrderStatus, UpsellStatus
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask
from app.modules.service_orders.schemas import (
    ClaimReviewInput,
    ServiceOrderDraftCreate,
    ServiceOrderDraftUpdate,
    UpsellCreate,
    UpsellTaskInput,
    WarrantyClaimFromWarranty,
)

visit_env = visit_fixtures.env


@pytest.mark.asyncio
async def test_web_can_persist_empty_draft_but_mobile_list_and_work_cannot_use_it(visit_env):
    service, session, _, origin, _, _, catalog = visit_env
    drafts = DraftService(service.db)
    actor = SimpleNamespace(user_id=origin.advisor_user_id)
    draft = await drafts.create(ServiceOrderDraftCreate(filial_id=origin.filial_id), actor)
    assert draft.is_draft and draft.vehicle_id is None and draft.creation_source == "web"
    assert draft.advisor_user_id == actor.user_id and draft.workshop_stage == "borrador"
    assert draft.id not in {o.id for o in await service.list_orders(origin.filial_id)}
    assert draft.id in {
        o.id for o in await service.list_orders(origin.filial_id, include_drafts=True)
    }
    with pytest.raises(ConflictError):
        await service.add_task(draft.id, catalog.id)
    with pytest.raises(ConflictError):
        await drafts.submit(draft.id, actor)
    await drafts.discard(draft.id)
    assert session.get(ServiceOrder, draft.id) is None


@pytest.mark.asyncio
async def test_autosave_checks_revision_and_sending_requires_task_and_actual_technician(
    visit_env, monkeypatch
):
    service, session, _, origin, _, _, catalog = visit_env
    drafts = DraftService(service.db)
    actor = SimpleNamespace(user_id=origin.advisor_user_id)
    draft = await drafts.create(
        ServiceOrderDraftCreate(filial_id=origin.filial_id, vehicle_id=origin.vehicle_id), actor
    )
    await drafts.update(
        draft.id, ServiceOrderDraftUpdate(expected_revision=1, customer_reason="Revisar frenos")
    )
    with pytest.raises(ConflictError) as err:
        await drafts.update(
            draft.id, ServiceOrderDraftUpdate(expected_revision=1, customer_reason="Desactualizado")
        )
    assert err.value.error_code == "draft_revision_conflict"
    assert draft.customer_reason == "Revisar frenos"
    task = await service.add_task(draft.id, catalog.id)
    assert draft.is_draft and draft.status == ServiceOrderStatus.PENDIENTE
    with pytest.raises(ConflictError):
        await service.start_task_timer(task.id)
    with pytest.raises(ConflictError):
        await drafts.submit(draft.id, actor)

    async def technician(*_args):
        return None

    monkeypatch.setattr(drafts, "_technician", technician)
    draft.technician_user_id = uuid.uuid4()
    session.commit()
    submitted = await drafts.submit(draft.id, actor)
    assert not submitted.is_draft and submitted.submitted_at
    assert submitted.id in {o.id for o in await service.list_orders(origin.filial_id)}
    with pytest.raises(ConflictError):
        await drafts.discard(draft.id)


@pytest.mark.asyncio
async def test_postponed_finding_is_not_approved_until_draft_is_sent(visit_env, monkeypatch):
    service, session, _, origin, _, _, catalog = visit_env
    finding = await service.create_upsell(
        origin.id,
        UpsellCreate(
            title="Revisar frenos",
            description="Pastillas gastadas",
            severity="pronto",
            tasks=[UpsellTaskInput(tempario_id=catalog.id)],
        ),
        [],
    )
    drafts = DraftService(service.db)
    actor = SimpleNamespace(user_id=origin.advisor_user_id)
    draft = await drafts.create(
        ServiceOrderDraftCreate(
            filial_id=origin.filial_id, vehicle_id=origin.vehicle_id, upsell_id=finding.id
        ),
        actor,
    )
    assert (await service.get_upsell(finding.id)).status == UpsellStatus.PENDIENTE
    assert len(await service.list_tasks(draft.id)) == 1

    async def technician(*_args):
        return None

    monkeypatch.setattr(drafts, "_technician", technician)
    draft.technician_user_id = uuid.uuid4()
    session.commit()
    await drafts.submit(draft.id, actor)
    resolved = await service.get_upsell(finding.id)
    assert resolved.status == UpsellStatus.APROBADO
    assert resolved.applied_to_service_order_id == draft.id
    assert resolved.resolved_by_user_id == actor.user_id


def test_claim_requires_one_coverage_and_rejection_reason():
    with pytest.raises(ValidationError):
        WarrantyClaimFromWarranty(vehicle_id=uuid.uuid4(), reported_mileage=1, symptom="Ruido")
    with pytest.raises(ValidationError):
        ClaimReviewInput(decision="rechazado")
    with pytest.raises(ValidationError):
        ClaimReviewInput(decision="rechazado", rejection_reason="   ")


@pytest.mark.asyncio
async def test_expired_coverage_cannot_create_claim_even_if_client_changes_ui(visit_env):
    from datetime import timedelta

    from app.core.venezuela_time import venezuela_today

    service, session, _, origin, _, _, catalog = visit_env
    task = await service.add_task(origin.id, catalog.id)
    # Coverage belongs to the vehicle, but elapsed before today.
    vehicle = await service._get_vehicle_with_client(origin.vehicle_id)
    vehicle.vin = "12345678901234567"
    today = venezuela_today()
    warranty = WorkshopWarranty(
        filial_id=origin.filial_id,
        vin=vehicle.vin,
        service_order_id=origin.id,
        service_order_task_id=task.id,
        tempario_code_snapshot=catalog.code,
        tempario_name_snapshot=catalog.name,
        starts_at=today - timedelta(days=100),
        duration_days=90,
        duration_km=5000,
        expires_at=today - timedelta(days=1),
        expiration_mileage=10000,
    )
    session.add(warranty)
    session.commit()
    with pytest.raises(ConflictError) as err:
        await ClaimsWorkflow(service.db).create(
            WarrantyClaimFromWarranty(
                vehicle_id=vehicle.id,
                workshop_warranty_id=warranty.id,
                reported_mileage=1000,
                symptom="Reapareció el ruido",
            ),
            [],
            [],
            SimpleNamespace(user_id=uuid.uuid4()),
        )
    assert err.value.error_code == "warranty_expired"


@pytest.mark.asyncio
async def test_draft_task_removal_removes_its_auto_parts(visit_env):
    # Even labor-only drafts remain pending after their final task is removed.
    service, session, _, origin, _, _, catalog = visit_env
    draft = await DraftService(service.db).create(
        ServiceOrderDraftCreate(filial_id=origin.filial_id, vehicle_id=origin.vehicle_id),
        SimpleNamespace(user_id=origin.advisor_user_id),
    )
    task = await service.add_task(draft.id, catalog.id)
    await service.delete_task(task.id)
    assert not session.scalars(
        select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == draft.id)
    ).all()
    assert draft.is_draft and draft.status == ServiceOrderStatus.PENDIENTE


@pytest.mark.asyncio
@pytest.mark.parametrize("campaign", [False, True])
async def test_factory_approval_opens_draft_without_brand_stage(visit_env, campaign):
    from datetime import timedelta

    from app.core.venezuela_time import venezuela_today
    from app.modules.post_ventas.models import VehicleWarranty
    from app.modules.service_orders.enums import WarrantyClaimStatus

    service, session, _, origin, _, _, _ = visit_env
    from app.modules.service_orders.models import ServiceOrderTypeCatalog
    from app.modules.service_orders.service import SYSTEM_ORDER_TYPES

    for kind in SYSTEM_ORDER_TYPES:
        if kind["code"] != "regular":
            session.add(ServiceOrderTypeCatalog(filial_id=origin.filial_id, **kind))
    session.commit()
    vehicle = await service._get_vehicle_with_client(origin.vehicle_id)
    vehicle.vin = "12345678901234567"
    today = venezuela_today()
    coverage = VehicleWarranty(
        filial_id=origin.filial_id,
        vin=vehicle.vin,
        brand=vehicle.brand,
        source="manual",
        starts_at=today,
        expires_at=today + timedelta(days=100),
        duration_km=100000,
    )
    session.add(coverage)
    session.commit()
    actor = SimpleNamespace(user_id=origin.advisor_user_id)
    flow = ClaimsWorkflow(service.db)
    claim = await flow.create(
        WarrantyClaimFromWarranty(
            vehicle_id=vehicle.id,
            factory_warranty_id=None if campaign else coverage.id,
            campaign=campaign,
            reported_mileage=500,
            symptom="No enfría el aire",
        ),
        [],
        [],
        actor,
    )
    reviewed = await flow.review(claim.id, ClaimReviewInput(decision="aprobado"), actor)
    assert reviewed.brand_status is None and reviewed.resulting_service_order_id is not None
    draft = await service.get_order(reviewed.resulting_service_order_id)
    assert draft.is_draft and draft.intake_mileage == 500
    assert (await service.get_order_type(draft.order_type_id)).code == "garantia_fabrica"
    assert reviewed.status == WarrantyClaimStatus.CONVERTIDO_A_ODS
    with pytest.raises(ConflictError):
        await flow.review(claim.id, ClaimReviewInput(decision="aprobado"), actor)



@pytest.mark.asyncio
async def test_comeback_approval_opens_order_and_keeps_client_payer_zero(visit_env):
    from datetime import UTC, datetime, timedelta

    from app.core.venezuela_time import venezuela_today
    from app.modules.service_orders.enums import ServiceOrderPayer, WarrantyClaimStatus
    from app.modules.service_orders.models import ServiceOrderInvoice

    service, session, _, origin, _, _, catalog = visit_env
    from app.modules.service_orders.models import ServiceOrderTypeCatalog
    from app.modules.service_orders.service import SYSTEM_ORDER_TYPES

    for kind in SYSTEM_ORDER_TYPES:
        if kind["code"] != "regular":
            session.add(ServiceOrderTypeCatalog(filial_id=origin.filial_id, **kind))
    session.commit()
    vehicle = await service._get_vehicle_with_client(origin.vehicle_id)
    vehicle.vin = "12345678901234567"
    task = await service.add_task(origin.id, catalog.id)
    session.add(
        ServiceOrderInvoice(
            service_order_id=origin.id,
            request_id=uuid.uuid4(),
            request_hash="h",
            code="FAC-WEB",
            issued_at=datetime.now(UTC),
            total_usd=50,
            document={},
            billed_client_id=vehicle.client_id,
            amount_paid_at_issuance=50,
        )
    )
    today = venezuela_today()
    warranty = WorkshopWarranty(
        filial_id=origin.filial_id,
        vin=vehicle.vin,
        service_order_id=origin.id,
        service_order_task_id=task.id,
        tempario_code_snapshot=catalog.code,
        tempario_name_snapshot=catalog.name,
        starts_at=today,
        duration_days=90,
        duration_km=5000,
        expires_at=today + timedelta(days=90),
        expiration_mileage=10000,
    )
    session.add(warranty)
    session.commit()
    flow = ClaimsWorkflow(service.db)
    actor = SimpleNamespace(user_id=origin.advisor_user_id)
    claim = await flow.create(
        WarrantyClaimFromWarranty(
            vehicle_id=vehicle.id,
            workshop_warranty_id=warranty.id,
            reported_mileage=1000,
            symptom="Reapareció el ruido",
        ),
        [],
        [],
        actor,
    )
    assert claim.service_order_id == origin.id and claim.tempario_id == catalog.id
    result = await flow.review(
        claim.id, ClaimReviewInput(decision="aprobado", failure_confirmed=True, failure_category="mano_de_obra"), actor
    )
    assert result.status == WarrantyClaimStatus.CONVERTIDO_A_ODS
    tasks = await service.list_tasks(result.resulting_service_order_id)
    assert tasks and all(t.payer == ServiceOrderPayer.GARANTIA_TALLER for t in tasks)
    assert (await flow.overview(origin.filial_id))[
        "monthly_cost"
    ] == 0  # No work yet, no fictional cost.


@pytest.mark.asyncio
async def test_calendar_cannot_double_book_a_bay_when_creating_or_moving_drafts(visit_env):
    from datetime import UTC, datetime, timedelta

    from app.modules.service_orders.models import Bay

    service, session, _, origin, _, _, _ = visit_env
    bay = Bay(filial_id=origin.filial_id, name="Bahía 1")
    session.add(bay)
    session.commit()
    actor = SimpleNamespace(user_id=origin.advisor_user_id)
    flow = DraftService(service.db)
    when = datetime(2026, 10, 20, 12, tzinfo=UTC)
    first = await flow.create(
        ServiceOrderDraftCreate(
            filial_id=origin.filial_id,
            vehicle_id=origin.vehicle_id,
            bay_id=bay.id,
            scheduled_at=when,
        ),
        actor,
    )
    with pytest.raises(ConflictError) as err:
        await flow.create(
            ServiceOrderDraftCreate(
                filial_id=origin.filial_id,
                vehicle_id=origin.vehicle_id,
                bay_id=bay.id,
                scheduled_at=when + timedelta(minutes=15),
            ),
            actor,
        )
    assert err.value.error_code == "bay_schedule_conflict"
    adjacent = await flow.create(
        ServiceOrderDraftCreate(
            filial_id=origin.filial_id,
            vehicle_id=origin.vehicle_id,
            bay_id=bay.id,
            scheduled_at=when + timedelta(minutes=30),
        ),
        actor,
    )
    with pytest.raises(ConflictError):
        await flow.update(adjacent.id, ServiceOrderDraftUpdate(scheduled_at=when))
    assert first.is_draft and adjacent.is_draft
