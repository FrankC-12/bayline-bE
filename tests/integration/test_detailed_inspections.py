import asyncio
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.detailed_inspections.models import DetailedInspection
from app.modules.detailed_inspections.router import router
from app.modules.detailed_inspections.schemas import InspectionData
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders.models import ServiceOrder
from app.modules.users.models import User


async def mobile(ctx, *, assigned=True):
    async with ctx.sessions() as db:
        role = Role(name="Técnico inspección", slug=str(uuid.uuid4()), scope=RoleScope.FILIAL)
        db.add(role)
        await db.flush()
        db.add(
            RoleModulePermission(
                role_id=role.id, module_id="tecnico-servicio", access=AccessLevel.EDITAR
            )
        )
        user = User(
            full_name="Técnico",
            email=f"{uuid.uuid4()}@example.com",
            role_id=role.id,
            filial_id=ctx.filial_id,
        )
        db.add(user)
        await db.flush()
        order = await db.get(ServiceOrder, ctx.order_id)
        if assigned:
            order.technician_user_id = user.id
        await db.commit()
        current = CurrentUser(
            user_id=user.id,
            email=user.email,
            role_id=role.id,
            role_slug=role.slug,
            scope="filial",
            filial_id=ctx.filial_id,
            holding_id=None,
        )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: current

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


def complete_payload():
    data = InspectionData().model_dump()
    for section in ("engine", "underbody", "brakes"):
        data[section] = {field: "bien" for field in data[section]}
    data["engine"].update(battery="cargada", battery_voltage=12.6)
    data["brakes"]["rear_shoes"] = "no_aplica"
    data["tires"] = {position: {"pressure": 32} for position in data["tires"]}
    return data


@pytest.mark.asyncio
async def test_mobile_detailed_inspection_progress_completion_and_annexes(billing_db, monkeypatch):
    ctx = billing_db
    app = await mobile(ctx)
    path = f"/api/v1/detailed-inspections/by-order/{ctx.order_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get(path)).json() is None
        template=(await http.get("/api/v1/detailed-inspections/template")).json()
        assert template["total_steps"] == 5
        assert "tread_depth" not in str(template["schema"])
        first = await http.patch(path, json={"engine": {"leaks": "regular"}})
        assert first.status_code == 200
        assert first.json()["completed_steps"] == 0
        saved = await http.patch(path, json={"engine": {"battery_voltage": 12.5}})
        assert saved.json()["data"]["engine"]["leaks"] == "regular"
        incomplete = await http.post(path + "/complete")
        assert incomplete.status_code == 422
        assert "engine.battery" in str(incomplete.json())
        saved = await http.patch(path, json=complete_payload())
        assert saved.json()["completed_steps"] == 4
        completed = await http.post(path + "/complete")
        assert completed.status_code == 200
        assert completed.json()["completed_steps"] == 5
        assert completed.json()["annexes"] == []
        assert (await http.post(path + "/complete")).json()["completed_at"] == completed.json()[
            "completed_at"
        ]
        assert (await http.patch(path, json={"observations": "cambio"})).status_code == 409
        assert (
            await http.post(path + "/annexes", files={"file": ("x.jpg", b"image", "image/jpeg")})
        ).status_code == 400

        async def save_pdf(file, **kwargs):
            return "/api/v1/uploads/detailed-inspections/scanner.pdf"

        monkeypatch.setattr(
            "app.modules.detailed_inspections.router.save_upload_attachment", save_pdf
        )
        upload = await http.post(
            path + "/annexes", files={"file": ("scanner.pdf", b"%PDF-1.4", "application/pdf")}
        )
        assert upload.status_code == 200
        assert upload.json()["completed_steps"] == 5
        annex = upload.json()["annexes"][0]
        assert annex["uploaded_by_user_id"] == completed.json()["completed_by_user_id"]
        removed = await http.delete(path + "/annexes/" + annex["id"])
        assert removed.status_code == 200
        assert removed.json()["annexes"] == []
        assert (await http.delete(path + "/annexes/" + annex["id"])).status_code == 404
        async with ctx.sessions() as db:
            assert (await db.get(ServiceOrder, ctx.order_id)).status.value == "completado"


@pytest.mark.asyncio
async def test_mobile_concurrent_draft_patches_merge_and_create_once(billing_db):
    app = await mobile(billing_db)
    path = f"/api/v1/detailed-inspections/by-order/{billing_db.order_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        responses = await asyncio.gather(
            http.patch(path, json={"engine": {"leaks": "bien"}}),
            http.patch(path, json={"tires": {"front_left": {"pressure": 35}}}),
        )
        assert all(response.status_code == 200 for response in responses)
        data = (await http.get(path)).json()["data"]
        assert data["engine"]["leaks"] == "bien"
        assert data["tires"]["front_left"]["pressure"] == 35
        cleared = await http.patch(path, json={"engine": {"leaks": None}})
        assert cleared.json()["data"]["engine"]["leaks"] is None
        assert cleared.json()["data"]["tires"]["front_left"]["pressure"] == 35
        async with billing_db.sessions() as db:
            assert (
                await db.execute(
                    select(func.count())
                    .select_from(DetailedInspection)
                    .where(DetailedInspection.service_order_id == billing_db.order_id)
                )
            ).scalar() == 1


@pytest.mark.asyncio
async def test_mobile_permissions_and_invalid_measurements(billing_db):
    app = await mobile(billing_db, assigned=False)
    path = f"/api/v1/detailed-inspections/by-order/{billing_db.order_id}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get(path)).status_code == 403
        assert (await http.patch(path, json={})).status_code == 403
        assert (await http.post(path + "/complete")).status_code == 403
    app = await mobile(billing_db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        for payload in (
            {"tires": {"spare": {"pressure": -1}}},
            {"engine": {"leaks": "no_aplica"}},
            {"brakes": {"unknown": "bien"}},
            {"status": "completada"},
        ):
            assert (await http.patch(path, json=payload)).status_code == 422
        assert (await http.get(path)).json() is None
