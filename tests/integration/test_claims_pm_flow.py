"""PM claim lifecycle: mandatory odometer evidence and one draft per approval."""

import asyncio
import base64
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.venezuela_time import venezuela_today
from app.modules.clients.models import Vehicle
from app.modules.post_ventas.models import WorkshopWarranty
from app.modules.roles.enums import AccessLevel
from app.modules.roles.models import RoleModulePermission
from app.modules.service_orders.billing import BillingService
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import (
    ServiceOrder,
    ServiceOrderTask,
    ServiceOrderTypeCatalog,
    WarrantyClaim,
)
from app.modules.service_orders.service import SYSTEM_ORDER_TYPES
from tests.integration.conftest import invoice_input
from tests.integration.test_catalog_pricing_and_evidence import application


@pytest.mark.asyncio
async def test_claim_photo_mileage_authorization_and_atomic_draft(
    billing_db, tmp_path, monkeypatch
):
    ctx = billing_db
    payload = await invoice_input(ctx)
    async with ctx.sessions() as db:
        await BillingService(db).issue(ctx.order_id, payload, None)
    app, state, actor = await application(ctx)
    settings = get_settings().model_copy(
        update={"uploads_dir": str(tmp_path), "s3_bucket_name": None}
    )
    monkeypatch.setattr("app.core.storage.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.service_orders.router.get_settings", lambda: settings)
    async with ctx.sessions() as db:
        existing_types = set(
            (
                await db.execute(
                    select(ServiceOrderTypeCatalog.code).where(
                        ServiceOrderTypeCatalog.filial_id == ctx.filial_id
                    )
                )
            ).scalars()
        )
        for entry in SYSTEM_ORDER_TYPES:
            if entry["code"] not in existing_types:
                db.add(ServiceOrderTypeCatalog(filial_id=ctx.filial_id, is_system=True, **entry))
        await db.flush()
        order = await db.get(ServiceOrder, ctx.order_id)
        order.status = ServiceOrderStatus.ORDEN_CERRADA
        order.closed_at = datetime.now(UTC)
        order.exit_mileage = 10000
        vehicle = await db.get(Vehicle, order.vehicle_id)
        vehicle.vin = "1HGCM82633A004352"
        task = (
            (
                await db.execute(
                    select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == order.id)
                )
            )
            .scalars()
            .first()
        )
        from datetime import timedelta

        existing_warranty = (
            await db.execute(
                select(WorkshopWarranty).where(
                    WorkshopWarranty.service_order_task_id == task.id,
                    WorkshopWarranty.coverage_type == "MANO_DE_OBRA",
                )
            )
        ).scalar_one_or_none()
        warranty = existing_warranty or WorkshopWarranty(
            filial_id=ctx.filial_id,
            vin=vehicle.vin,
            service_order_id=order.id,
            service_order_task_id=task.id,
            tempario_code_snapshot=task.code_snapshot,
            tempario_name_snapshot=task.name_snapshot,
            starts_at=venezuela_today(),
            duration_days=90,
            duration_km=5000,
            expires_at=venezuela_today() + timedelta(days=90),
            expiration_mileage=15000,
        )
        warranty.starts_at = venezuela_today()
        warranty.duration_days = 90
        warranty.duration_km = 5000
        warranty.expires_at = venezuela_today() + timedelta(days=90)
        warranty.expiration_mileage = 15000
        db.add(warranty)
        db.add(
            RoleModulePermission(
                role_id=actor.role_id, module_id="asesor-servicios", access=AccessLevel.EDITAR
            )
        )
        await db.commit()
        vehicle_id, warranty_id = vehicle.id, warranty.id
        source_name, source_hours = task.name_snapshot, task.hours_snapshot
        from app.modules.post_ventas.models import Tempario

        catalog = await db.get(Tempario, task.tempario_id)
        catalog.name = "Nombre cambiado después del servicio"
        catalog.estimated_hours = 8
        await db.commit()
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/x9sAAAAASUVORK5CYII="
    )
    form = {
        "vehicle_id": str(vehicle_id),
        "workshop_warranty_id": str(warranty_id),
        "reported_mileage": "10050",
        "symptom": "Fuga después del cambio",
    }
    files = lambda: {"odometer_photo": ("tablero.png", png, "image/png")}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        missing = await http.post("/api/v1/warranty-claims/from-warranty", data=form)
        assert missing.status_code == 422
        low = await http.post(
            "/api/v1/warranty-claims/from-warranty",
            data={**form, "reported_mileage": "9999"},
            files=files(),
        )
        assert low.status_code == 409, low.text
        invalid = await http.post(
            "/api/v1/warranty-claims/from-warranty",
            data=form,
            files={"odometer_photo": ("tablero.pdf", b"invalid", "application/pdf")},
        )
        assert invalid.status_code == 400
        created = await http.post("/api/v1/warranty-claims/from-warranty", data=form, files=files())
        assert created.status_code == 201, created.text
        claim = created.json()
        assert claim["claimed_at"] == venezuela_today().isoformat()
        assert (
            claim["odometer_photo_url"] and claim["warranty_assessment"]["distance"]["used"] == 50
        )
        assert claim["warranty_assessment"]["valid"] is True
        path = f'/api/v1/warranty-claims/{claim["id"]}/review'
        decision = {
            "decision": "aprobado",
            "failure_category": "mano_de_obra",
            "failure_confirmed": True,
        }
        state["actor"] = actor
        assert (await http.post(path, json=decision)).status_code == 403
        state["actor"] = actor.model_copy(update={"role_slug": "filial-admin"})
        missing_reason = await http.post(
            path, json={"decision": "rechazado", "rejection_reason": " "}
        )
        assert missing_reason.status_code == 422
        unconfirmed = await http.post(path, json={**decision, "failure_confirmed": False})
        assert unconfirmed.status_code == 400
        responses = await asyncio.gather(
            http.post(path, json=decision), http.post(path, json=decision)
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        approved = next(response.json() for response in responses if response.status_code == 200)
        assert approved["brand_status"] is None and approved["failure_confirmed"] is True
        draft_id = approved["resulting_service_order_id"]
        draft = await http.get(f"/api/v1/service-orders/{draft_id}")
        assert draft.status_code == 200, draft.text
        assert draft.json()["is_draft"] and draft.json()["intake_mileage"] == 10050
        summary = await http.get(f"/api/v1/service-orders/{draft_id}/summary")
        assert summary.status_code == 200 and summary.json()["total"] == 0
        legacy = await http.post(f'/api/v1/warranty-claims/{claim["id"]}/convert-to-order', json={})
        assert legacy.status_code == 200 and legacy.json()["resulting_service_order_id"] == draft_id
    async with ctx.sessions() as db:
        assert (
            await db.scalar(
                select(func.count(WarrantyClaim.id)).where(WarrantyClaim.vehicle_id == vehicle_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count(ServiceOrder.id)).where(
                    ServiceOrder.warranty_claim_id == claim["id"]
                )
            )
            == 1
        )

        new_task = (
            (
                await db.execute(
                    select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == draft_id)
                )
            )
            .scalars()
            .one()
        )
        assert new_task.name_snapshot == source_name and new_task.hours_snapshot == source_hours
