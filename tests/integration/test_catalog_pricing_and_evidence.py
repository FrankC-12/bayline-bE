"""Catalog onboarding, prices and mobile evidence through real HTTP/PostgreSQL."""

import base64
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.ajustes.router import router as settings_router
from app.modules.auth.dependencies import get_current_user
from app.modules.detailed_inspections.router import router as detailed_router
from app.modules.inspections.router import router as inspections_router
from app.modules.post_ventas.router import router as catalog_router
from app.modules.service_orders.models import ServiceOrder, ServiceOrderTask
from app.modules.service_orders.router import router as orders_router
from app.modules.vehicle_catalog.models import VehicleBrand
from tests.integration.test_mobile_workshop import technician


async def application(ctx):
    actor = await technician(ctx)
    state = {"actor": actor.model_copy(update={"role_slug": "filial-admin"})}
    app = FastAPI()
    register_exception_handlers(app)
    for router in [
        settings_router,
        detailed_router,
        inspections_router,
        catalog_router,
        orders_router,
    ]:
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: state["actor"]

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app, state, actor


@pytest.mark.asyncio
async def test_reviewed_bulk_import_is_atomic_and_requires_settings_permission(billing_db):
    ctx = billing_db
    app, state, actor = await application(ctx)
    name = f"Marca {uuid.uuid4().hex[:8]}"
    path = "/api/v1/settings/bulk/marcas"
    query = f"?filial_id={ctx.filial_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        templates = await http.get("/api/v1/settings/bulk/templates" + query)
        assert templates.status_code == 200 and len(templates.json()) == 8
        payload = {"rows": [{"nombre": name, "activo": "si"}]}
        preview = await http.post(path + "/preview" + query, json=payload)
        assert preview.status_code == 200 and preview.json()["errors"] == []
        async with ctx.sessions() as db:
            assert not await db.scalar(select(VehicleBrand.id).where(VehicleBrand.name == name))
        payload["review_hash"] = preview.json()["review_hash"]
        imported = await http.post(path + "/import" + query, json=payload)
        assert imported.status_code == 200 and imported.json()["imported"] == 1
        invalid = {"rows": [{"nombre": name + " válido"}, {"nombre": ""}]}
        preview = await http.post(path + "/preview" + query, json=invalid)
        assert len(preview.json()["errors"]) == 1
        invalid["review_hash"] = preview.json()["review_hash"]
        assert (await http.post(path + "/import" + query, json=invalid)).status_code == 400
        async with ctx.sessions() as db:
            assert not await db.scalar(
                select(VehicleBrand.id).where(VehicleBrand.name == name + " válido")
            )
        state["actor"] = actor
        assert (await http.post(path + "/preview" + query, json=payload)).status_code == 403
        assert (
            await http.get("/api/v1/settings/bulk/templates?filial_id=" + str(uuid.uuid4()))
        ).status_code == 403


@pytest.mark.asyncio
async def test_mobile_fluids_and_real_uploaded_photos_are_visible_in_order_records(
    billing_db, tmp_path, monkeypatch
):
    ctx = billing_db
    app, _, _ = await application(ctx)
    settings = get_settings().model_copy(
        update={"uploads_dir": str(tmp_path), "s3_bucket_name": None}
    )
    monkeypatch.setattr("app.core.storage.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.inspections.router.get_settings", lambda: settings)
    monkeypatch.setattr("app.modules.detailed_inspections.router.get_settings", lambda: settings)
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        vehicle_id = order.vehicle_id
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/x9sAAAAASUVORK5CYII="
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        preliminary = await http.post(
            "/api/v1/inspections",
            json={
                "filial_id": str(ctx.filial_id),
                "vehicle_id": str(vehicle_id),
                "mileage": 10000,
                "notes": "Recepción con fluidos",
                "interior": {
                    "seats": {
                        "condition": "regular",
                        "notes": "Tapicería revisada",
                        "photo_urls": ["/uploads/interior.png"],
                    },
                    "odometer_display": 0,
                },
                "fluids": {"engine_oil": {"level": "bajo", "notes": "Revisar aceite"}},
            },
        )
        assert preliminary.status_code == 201, preliminary.text
        inspection_id = preliminary.json()["id"]
        linked = await http.patch(
            f"/api/v1/inspections/{inspection_id}", json={"service_order_id": str(ctx.order_id)}
        )
        assert linked.status_code == 200, linked.text
        photos = await http.post(
            f"/api/v1/inspections/{inspection_id}/photos",
            files=[("photos", ("recepcion.png", png, "image/png"))],
        )
        assert photos.status_code == 200, photos.text
        path = f"/api/v1/detailed-inspections/by-order/{ctx.order_id}"
        detailed = await http.patch(
            path,
            json={
                "fluids": {"coolant": {"level": "regular", "notes": "Refrigerante revisado"}},
                "engine": {"battery_voltage": 0},
                "interior": {"seatbelts": "bien"},
            },
        )
        assert detailed.status_code == 200, detailed.text
        uploaded = await http.post(
            path + "/photos", files=[("photos", ("motor.png", png, "image/png"))]
        )
        assert uploaded.status_code == 200, uploaded.text
        records = await http.get(f"/api/v1/service-orders/{ctx.order_id}/inspection-records")
        assert records.status_code == 200, records.text
        data = records.json()
        assert data["preliminary"]["fluids"]["engine_oil"]["level"] == "bajo"
        assert data["preliminary"]["interior"]["seats"]["photo_urls"] == ["/uploads/interior.png"]
        assert data["preliminary"]["interior"]["odometer_display"] == 0
        assert data["detailed"]["data"]["interior"]["seatbelts"] == "bien"
        assert len(data["preliminary"]["photo_urls"]) == 1
        assert data["detailed"]["data"]["fluids"]["coolant"]["notes"] == "Refrigerante revisado"
        assert data["detailed"]["data"]["engine"]["battery_voltage"] == 0
        assert data["detailed"]["annexes"][0]["filename"] == "motor.png"
        assert len(list(tmp_path.rglob("*.png"))) == 2


@pytest.mark.asyncio
async def test_contextual_quote_is_read_only_and_price_mismatch_rolls_back_task(billing_db):
    ctx = billing_db
    app, _, _ = await application(ctx)
    async with ctx.sessions() as db:
        order = await db.get(ServiceOrder, ctx.order_id)
        order.settings_snapshot = {"hourly_rate": 12.70, "iva_percentage": 8}
        task = await db.scalar(
            select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == order.id)
        )
        tempario_id = task.tempario_id
        await db.commit()
        before = await db.scalar(
            select(func.count())
            .select_from(ServiceOrderTask)
            .where(ServiceOrderTask.service_order_id == order.id)
        )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        quote = await http.get(
            f"/api/v1/service-orders/{ctx.order_id}/temparios/{tempario_id}/quote"
        )
        assert quote.status_code == 200, quote.text
        assert quote.json()["hourly_rate"] == 12.7 and quote.json()["iva_percentage"] == 8
        rejected = await http.post(
            f"/api/v1/service-orders/{ctx.order_id}/tasks",
            json={
                "tempario_id": str(tempario_id),
                "expected_total": quote.json()["total_with_iva"] + 1,
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["errorCode"] == "task_quote_changed"
    async with ctx.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ServiceOrderTask)
                .where(ServiceOrderTask.service_order_id == ctx.order_id)
            )
            == before
        )


@pytest.mark.asyncio
async def test_factory_km_settings_and_parts_only_catalog_access(billing_db):
    from app.modules.roles.enums import AccessLevel
    from app.modules.roles.models import RoleModulePermission

    ctx = billing_db
    app, state, actor = await application(ctx)
    query = f"?filial_id={ctx.filial_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        patched = await http.patch(
            "/api/v1/settings" + query, json={"vehicle_warranty_default_km": 100000}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["vehicle_warranty_default_km"] == 100000
        read = await http.get("/api/v1/settings" + query)
        assert read.json()["vehicle_warranty_default_km"] == 100000
        assert (await http.get("/api/v1/service-order-types" + query)).status_code == 200
        async with ctx.sessions() as db:
            db.add(
                RoleModulePermission(
                    role_id=actor.role_id, module_id="repuestos", access=AccessLevel.VER
                )
            )
            await db.commit()
        state["actor"] = actor
        catalog = await http.get("/api/v1/temparios" + query)
        assert catalog.status_code == 200, catalog.text
        assert (
            await http.get("/api/v1/temparios" + query + "&part_id=" + str(uuid.uuid4()))
        ).json() == []
        assert (
            await http.get("/api/v1/temparios?filial_id=" + str(uuid.uuid4()))
        ).status_code == 403
        create = await http.post(
            "/api/v1/temparios",
            json={
                "filial_id": str(ctx.filial_id),
                "category": "motor",
                "name": "No permitido",
                "estimated_hours": 1,
            },
        )
        assert create.status_code == 403
