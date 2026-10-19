"""Real web drafts and mobile visibility against PostgreSQL and the HTTP API."""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.service_orders.drafts import DraftService
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask
from app.modules.service_orders.router import router
from app.modules.service_orders.schemas import ServiceOrderDraftCreate, ServiceOrderDraftUpdate
from app.modules.service_orders.service import ServiceOrderService
from tests.integration.test_mobile_workshop import technician


@pytest.mark.asyncio
async def test_web_draft_roundtrip_hides_from_mobile_then_submit_appears(billing_db):
    ctx = billing_db
    mobile = await technician(ctx)
    advisor = mobile.model_copy(update={"role_slug": "filial-admin"})
    active = advisor
    async with ctx.sessions() as db:
        source = await db.get(ServiceOrder, ctx.order_id)
        vehicle_id = str(source.vehicle_id)
        catalog_id = str(
            (
                await db.execute(
                    select(ServiceOrderTask.tempario_id).where(
                        ServiceOrderTask.service_order_id == source.id
                    )
                )
            ).scalar_one()
        )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: active

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        created = await http.post(
            "/api/v1/service-order-drafts", json={"filial_id": str(ctx.filial_id)}
        )
        assert created.status_code == 201, created.text
        draft = created.json()
        key = draft["id"]
        assert draft["vehicle_id"] is None
        saved = await http.patch(
            f"/api/v1/service-order-drafts/{key}",
            json={
                "vehicle_id": vehicle_id,
                "technician_user_id": str(mobile.user_id),
                "customer_reason": "Ruido al frenar",
                "expected_revision": 1,
            },
        )
        assert saved.status_code == 200, saved.text
        stale = await http.patch(
            f"/api/v1/service-order-drafts/{key}",
            json={"customer_reason": "Viejo", "expected_revision": 1},
        )
        assert stale.status_code == 409
        task = await http.post(
            f"/api/v1/service-orders/{key}/tasks", json={"tempario_id": catalog_id}
        )
        assert task.status_code in (200, 201), task.text
        active = mobile
        hidden = await http.get(
            f"/api/v1/service-orders?filial_id={ctx.filial_id}&include_drafts=true"
        )
        assert hidden.status_code == 200, hidden.text
        assert key not in {o["id"] for o in hidden.json()}
        assert (await http.get(f"/api/v1/service-orders/{key}")).status_code == 403
        active = advisor
        sent = await http.post(f"/api/v1/service-order-drafts/{key}/submit")
        assert sent.status_code == 200, sent.text
        assert not sent.json()["is_draft"]
        active = mobile
        visible = await http.get(f"/api/v1/service-orders?filial_id={ctx.filial_id}")
        assert key in {o["id"] for o in visible.json()}
        records = await http.get(f"/api/v1/service-orders/{key}/inspection-records")
        assert records.status_code == 200, records.text
        assert records.json()["preliminary"] is None


@pytest.mark.asyncio
async def test_concurrent_draft_creation_and_autosave_are_serialized(billing_db):
    ctx = billing_db
    actor = await technician(ctx)

    async def create():
        async with ctx.sessions() as db:
            return await DraftService(db).create(
                ServiceOrderDraftCreate(filial_id=ctx.filial_id), actor
            )

    first, second = await asyncio.gather(create(), create())
    assert first.sequence_number != second.sequence_number
    from app.core.exceptions import ConflictError

    async def update(reason):
        async with ctx.sessions() as db:
            try:
                return await DraftService(db).update(
                    first.id, ServiceOrderDraftUpdate(expected_revision=1, customer_reason=reason)
                )
            except ConflictError as err:
                return err

    results = await asyncio.gather(update("Primera edición"), update("Segunda edición"))
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    async with ctx.sessions() as db:
        await DraftService(db).discard(first.id)
        await DraftService(db).discard(second.id)


@pytest.mark.asyncio
async def test_assigned_mobile_technician_can_create_finding_and_cannot_impersonate_another(
    billing_db,
):
    import json
    import uuid

    ctx = billing_db
    tech = await technician(ctx)
    active = tech
    async with ctx.sessions() as db:
        catalog_id = (
            await db.execute(
                select(ServiceOrderTask.tempario_id).where(
                    ServiceOrderTask.service_order_id == ctx.order_id
                )
            )
        ).scalar_one()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: active

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    data = {
        "title": "Pastillas delanteras",
        "description": "Quedan al veinte por ciento",
        "severity": "pronto",
        "tasks_json": json.dumps([{"tempario_id": str(catalog_id)}]),
        "detected_by_user_id": str(uuid.uuid4()),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        invalid = await http.post(
            f"/api/v1/service-orders/{ctx.order_id}/upsells",
            data={**data, "tasks_json": "not json"},
        )
        assert invalid.status_code == 422, invalid.text
        response = await http.post(f"/api/v1/service-orders/{ctx.order_id}/upsells", data=data)
        assert response.status_code == 201, response.text
        assert response.json()["detected_by_user_id"] == str(tech.user_id)
        active = tech.model_copy(update={"user_id": uuid.uuid4()})
        assert (
            await http.post(f"/api/v1/service-orders/{ctx.order_id}/upsells", data=data)
        ).status_code == 403


@pytest.mark.asyncio
async def test_draft_auto_parts_are_removed_and_discard_never_moves_stock(billing_db):
    from app.core.exceptions import ConflictError
    from app.modules.parts.models import Part
    from app.modules.post_ventas.models import TemparioPart
    from app.modules.service_orders.dispatch import WorkshopDispatchService
    from app.modules.service_orders.models import (
        ServiceOrderTransfer,
    )
    from tests.integration.test_workshop_dispatch_postgres import inventory

    ctx = billing_db
    actor = await technician(ctx)
    part_id, _, _ = await inventory(ctx)
    async with ctx.sessions() as db:
        original = await db.get(ServiceOrder, ctx.order_id)
        catalog = (
            await db.execute(
                select(ServiceOrderTask.tempario_id).where(
                    ServiceOrderTask.service_order_id == original.id
                )
            )
        ).scalar_one()
        db.add(
            TemparioPart(
                tempario_id=catalog, part_id=part_id, name="Aceite", quantity=2, unit_cost=10
            )
        )
        await db.commit()
        flow = DraftService(db)
        service = ServiceOrderService(db)
        draft = await flow.create(
            ServiceOrderDraftCreate(filial_id=ctx.filial_id, vehicle_id=original.vehicle_id), actor
        )
        task = await service.add_task(draft.id, catalog)
        draft_id, task_id = draft.id, task.id
        transfers = await service.list_transfers(draft_id)
        assert len(transfers) == 1 and transfers[0].lines[0].quantity == 2
        with pytest.raises(ConflictError):
            await WorkshopDispatchService(db).submit(transfers[0].id)
        await db.rollback()
        await service.delete_task(task_id)
        assert not await service.list_transfers(draft_id)
        await service.add_task(draft_id, catalog)
        await flow.discard(draft_id)
        assert await db.get(ServiceOrder, draft_id) is None
        assert (await db.get(Part, part_id)).stock_quantity == 5
        assert (
            not (
                await db.execute(
                    select(ServiceOrderTransfer.id).where(
                        ServiceOrderTransfer.service_order_id == draft_id
                    )
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_inspection_records_remain_readable_after_order_is_closed(billing_db):
    import uuid
    from datetime import UTC, datetime

    from app.modules.detailed_inspections.models import DetailedInspection
    from app.modules.inspections.models import InspectionDamage, PreliminaryInspection
    from app.modules.service_orders.enums import ServiceOrderStatus

    ctx = billing_db
    actor = await technician(ctx)
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        order.status = ServiceOrderStatus.ORDEN_CERRADA
        inspection = PreliminaryInspection(
            filial_id=ctx.filial_id,
            vehicle_id=order.vehicle_id,
            service_order_id=order.id,
            inspector_user_id=actor.user_id,
            mileage=45000,
            notes="Rayón registrado al recibir",
            photo_urls=["/api/v1/uploads/odometer.jpg"],
        )
        db.add(inspection)
        await db.flush()
        db.add(
            InspectionDamage(
                inspection_id=inspection.id,
                x=0.4,
                y=0.5,
                zone="puerta",
                kind="rayon",
                severity="leve",
                description="Puerta izquierda",
                photo_url="/api/v1/uploads/damage.jpg",
            )
        )
        db.add(
            DetailedInspection(
                service_order_id=order.id,
                created_by_user_id=actor.user_id,
                updated_by_user_id=actor.user_id,
                data={"engine": {"battery_voltage": 12.6}, "observations": "Revisar la fuga"},
                annexes=[
                    {
                        "id": str(uuid.uuid4()),
                        "filename": "fuga.jpg",
                        "url": "/api/v1/uploads/fuga.jpg",
                        "uploaded_at": datetime.now(UTC).isoformat(),
                        "uploaded_by_user_id": str(actor.user_id),
                    }
                ],
            )
        )
        await db.commit()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: actor

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get(f"/api/v1/service-orders/{ctx.order_id}/inspection-records")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["preliminary"]["mileage"] == 45000
        assert data["preliminary"]["damages"][0]["description"] == "Puerta izquierda"
        assert data["preliminary"]["photo_urls"]
        assert data["detailed"]["data"]["engine"]["battery_voltage"] == 12.6
        assert data["detailed"]["annexes"][0]["filename"] == "fuga.jpg"
