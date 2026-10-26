"""Mobile contracts exercised with PostgreSQL locks and real HTTP authorization."""

import asyncio
import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import ConflictError
from app.core.security import hash_password
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.exceptions import InvalidRefreshTokenError
from app.modules.auth.schemas import CurrentUser
from app.modules.auth.service import AuthService
from app.modules.filiales.models import Filial
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.models import Bay, ServiceOrder, ServiceOrderTask
from app.modules.service_orders.router import router
from app.modules.service_orders.service import ServiceOrderService
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


async def technician(ctx):
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        role = Role(name="Técnico mobile", slug=f"tec-{uuid.uuid4().hex}", scope=RoleScope.FILIAL)
        db.add(role)
        await db.flush()
        db.add(
            RoleModulePermission(
                role_id=role.id, module_id="tecnico-servicio", access=AccessLevel.EDITAR
            )
        )
        user = User(
            full_name="Técnico mobile",
            email=f"{uuid.uuid4().hex}@example.com",
            password_hash=hash_password("Mobile123"),
            status=UserStatus.ACTIVO,
            role_id=role.id,
            filial_id=ctx.filial_id,
            holding_id=filial.holding_id,
        )
        db.add(user)
        await db.flush()
        order = await db.get(ServiceOrder, ctx.order_id)
        order.technician_user_id = user.id
        order.status = ServiceOrderStatus.PENDIENTE
        await db.commit()
        return CurrentUser(
            user_id=user.id,
            email=user.email,
            role_id=role.id,
            role_slug=role.slug,
            scope=RoleScope.FILIAL,
            filial_id=ctx.filial_id,
            holding_id=filial.holding_id,
        )


@pytest.mark.asyncio
async def test_parallel_timer_start_serializes_across_a_technicians_orders(billing_db):
    ctx = billing_db
    user = await technician(ctx)
    async with ctx.sessions() as db:
        first = await db.get(ServiceOrder, ctx.order_id)
        task = (
            (
                await db.execute(
                    select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == first.id)
                )
            )
            .scalars()
            .first()
        )
        second = ServiceOrder(
            filial_id=first.filial_id,
            vehicle_id=first.vehicle_id,
            order_type_id=first.order_type_id,
            sequence_number=2,
            technician_user_id=user.user_id,
        )
        db.add(second)
        await db.flush()
        other = ServiceOrderTask(
            service_order_id=second.id,
            tempario_id=task.tempario_id,
            code_snapshot=task.code_snapshot,
            name_snapshot=task.name_snapshot,
            hours_snapshot=task.hours_snapshot,
        )
        db.add(other)
        from app.modules.inspections.models import PreliminaryInspection
        from app.modules.inspections.enums import InspectionStatus
        from app.modules.detailed_inspections.models import DetailedInspection

        for order in (first, second):
            db.add(
                PreliminaryInspection(
                    filial_id=order.filial_id,
                    vehicle_id=order.vehicle_id,
                    service_order_id=order.id,
                    inspector_user_id=user.user_id,
                    status=InspectionStatus.COMPLETADA,
                    notes="Recepción verificada",
                )
            )
            db.add(
                DetailedInspection(
                    service_order_id=order.id,
                    status="completada",
                    created_by_user_id=user.user_id,
                    updated_by_user_id=user.user_id,
                )
            )
        await db.commit()
        ids = task.id, other.id

    async def start(task_id):
        async with ctx.sessions() as db:
            try:
                await ServiceOrderService(db).start_task_timer(task_id)
                return "started"
            except ConflictError as exc:
                return exc.error_code

    results = await asyncio.gather(*(start(task_id) for task_id in ids))
    assert sorted(results) == ["started", "technician_timer_running"]


@pytest.mark.asyncio
async def test_refresh_token_cannot_be_rotated_twice_concurrently(billing_db):
    ctx = billing_db
    user = await technician(ctx)
    async with ctx.sessions() as db:
        tokens = await AuthService(db).login(user.email, "Mobile123")

    async def refresh():
        async with ctx.sessions() as db:
            try:
                await AuthService(db).refresh(tokens.refresh_token)
                return "renewed"
            except InvalidRefreshTokenError:
                return "rejected"

    assert sorted(await asyncio.gather(refresh(), refresh())) == ["rejected", "renewed"]


@pytest.mark.asyncio
async def test_technician_can_read_only_own_filial_bays_and_store_own_task_notes(billing_db):
    ctx = billing_db
    user = await technician(ctx)
    async with ctx.sessions() as db:
        db.add(Bay(filial_id=ctx.filial_id, name="Bahía mobile"))
        task = (
            (
                await db.execute(
                    select(ServiceOrderTask).where(
                        ServiceOrderTask.service_order_id == ctx.order_id
                    )
                )
            )
            .scalars()
            .first()
        )
        task_id = task.id
        await db.commit()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get(f"/api/v1/bays?filial_id={ctx.filial_id}")).status_code == 200
        assert (await http.get(f"/api/v1/bays?filial_id={uuid.uuid4()}")).status_code == 403
        received = await http.post(f"/api/v1/service-orders/{ctx.order_id}/vehicle-received")
        assert received.status_code == 200
        assert received.json()["vehicle_received_at"]
        notes = await http.patch(
            f"/api/v1/service-order-tasks/{task_id}/notes", json={"notes": "Se revisó la fuga"}
        )
        assert notes.status_code == 200
        assert notes.json()["notes"] == "Se revisó la fuga"
        user = user.model_copy(update={"user_id": uuid.uuid4()})
        assert (
            await http.patch(
                f"/api/v1/service-order-tasks/{task_id}/notes", json={"notes": "No autorizado"}
            )
        ).status_code == 403


@pytest.mark.asyncio
async def test_create_order_rolls_back_all_accepted_recommendations_on_any_failure(billing_db):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func

    from app.modules.service_orders.models import Upsell
    from app.modules.service_orders.schemas import UpsellCreate, UpsellTaskInput

    ctx = billing_db
    user = (await technician(ctx)).model_copy(update={"role_slug": "filial-admin"})
    async with ctx.sessions() as db:
        origin = await db.get(ServiceOrder, ctx.order_id)
        task = (
            (
                await db.execute(
                    select(ServiceOrderTask).where(ServiceOrderTask.service_order_id == origin.id)
                )
            )
            .scalars()
            .first()
        )
        finding = await ServiceOrderService(db).create_upsell(
            origin.id,
            UpsellCreate(
                title="Trabajo adicional",
                description="Revisar motor",
                severity="pronto",
                tasks=[UpsellTaskInput(tempario_id=task.tempario_id)],
            ),
            [],
        )
        finding_id = finding.id
        origin.status = ServiceOrderStatus.ORDEN_CERRADA
        await db.commit()
        payload = {
            "filial_id": str(ctx.filial_id),
            "vehicle_id": str(origin.vehicle_id),
            "advisor_user_id": str(user.user_id),
            "customer_reason": "Trabajo autorizado",
            "scheduled_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            "accepted_upsells": [
                {"upsell_id": str(finding_id), "approval_channel": "presencial"},
                {"upsell_id": str(uuid.uuid4()), "approval_channel": "presencial"},
            ],
        }
        before = (
            await db.execute(
                select(func.count())
                .select_from(ServiceOrder)
                .where(ServiceOrder.filial_id == ctx.filial_id)
            )
        ).scalar_one()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: user

    async def session():
        async with ctx.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/api/v1/service-orders", json=payload)
        assert response.status_code == 404
    async with ctx.sessions() as db:
        after = (
            await db.execute(
                select(func.count())
                .select_from(ServiceOrder)
                .where(ServiceOrder.filial_id == ctx.filial_id)
            )
        ).scalar_one()
        assert after == before
        finding = await db.get(Upsell, finding_id)
        assert finding.status.value == "pendiente"
        assert finding.applied_to_service_order_id is None
