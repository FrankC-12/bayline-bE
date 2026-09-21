"""Cancelling a service order now requires a confirmed, mandatory reason and
leaves who/when it was cancelled; reopening a cancelled order requires the
"administracion" module (not the asesor's own asesor-servicios) and leaves
its own who/when trail."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exception_handlers import register_exception_handlers
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser
from app.modules.filiales.models import Filial
from app.modules.holdings.models import Holding
from app.modules.roles.enums import AccessLevel, RoleScope
from app.modules.roles.models import Role, RoleModulePermission
from app.modules.service_orders import router as routes
from app.modules.service_orders.enums import ServiceOrderStatus
from app.modules.service_orders.exceptions import (
    InvalidStatusTransitionError,
    ServiceOrderNotCancelledError,
    ServiceOrderReadOnlyError,
)
from app.modules.service_orders.models import ServiceOrder
from app.modules.service_orders.schemas import ServiceOrderUpdate
from app.modules.service_orders.service import ServiceOrderService


def _user(*, role_id, role_slug, filial_id) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), email="user@test.com", role_id=role_id, role_slug=role_slug,
        scope=RoleScope.FILIAL, holding_id=None, filial_id=filial_id,
    )


@pytest.fixture
def env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        holding = Holding(name="Holding", slug="holding")
        session.add(holding)
        session.commit()
        filial = Filial(holding_id=holding.id, name="Filial", slug="filial")
        session.add(filial)
        asesor_role = Role(name="Asesor", slug="asesor", scope=RoleScope.FILIAL)
        admin_role = Role(name="Administrador", slug="administrador", scope=RoleScope.FILIAL)
        session.add_all([asesor_role, admin_role])
        session.commit()
        session.add_all(
            [
                RoleModulePermission(
                    role_id=asesor_role.id, module_id="asesor-servicios", access=AccessLevel.EDITAR
                ),
                RoleModulePermission(
                    role_id=admin_role.id, module_id="asesor-servicios", access=AccessLevel.EDITAR
                ),
                RoleModulePermission(
                    role_id=admin_role.id, module_id="administracion", access=AccessLevel.EDITAR
                ),
            ]
        )
        order = ServiceOrder(
            id=uuid.uuid4(), filial_id=filial.id, vehicle_id=uuid.uuid4(), sequence_number=2001
        )
        session.add(order)
        session.commit()

        app = FastAPI()
        app.include_router(routes.router, prefix="/api/v1")
        register_exception_handlers(app)
        db = AsyncAdapter(session)
        app.dependency_overrides[routes.get_service] = lambda: ServiceOrderService(db)

        asesor = _user(role_id=asesor_role.id, role_slug="asesor", filial_id=filial.id)
        admin = _user(role_id=admin_role.id, role_slug="administrador", filial_id=filial.id)
        yield app, session, order, asesor, admin


def _client_as(app, user: CurrentUser) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- Service layer -------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order_records_reason_actor_and_timestamp(env):
    _app, session, order, asesor, _admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    cancelled = await service.cancel_order(order.id, "El cliente desistió.", asesor.user_id)
    assert cancelled.status == ServiceOrderStatus.CANCELADO
    assert cancelled.cancel_reason == "El cliente desistió."
    assert cancelled.cancelled_by_user_id == asesor.user_id
    assert cancelled.cancelled_at is not None
    assert cancelled.reopened_by_user_id is None
    assert cancelled.reopened_at is None


@pytest.mark.asyncio
async def test_cancel_order_from_completado_is_rejected(env):
    """COMPLETADO is still editable (require_editable_order lets it through)
    but isn't a status ALLOWED_TRANSITIONS lets move to CANCELADO."""
    _app, session, order, asesor, _admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    order.status = ServiceOrderStatus.COMPLETADO
    session.commit()
    with pytest.raises(InvalidStatusTransitionError):
        await service.cancel_order(order.id, "Motivo", asesor.user_id)


@pytest.mark.asyncio
async def test_cancel_order_from_a_terminal_status_is_read_only(env):
    _app, session, order, asesor, _admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    order.status = ServiceOrderStatus.ORDEN_CERRADA
    session.commit()
    with pytest.raises(ServiceOrderReadOnlyError):
        await service.cancel_order(order.id, "Motivo", asesor.user_id)


@pytest.mark.asyncio
async def test_cancelled_order_becomes_read_only(env):
    _app, session, order, asesor, _admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    await service.cancel_order(order.id, "Motivo", asesor.user_id)
    with pytest.raises(ServiceOrderReadOnlyError):
        await service.update_order(order.id, ServiceOrderUpdate(notes="cualquier cambio"))


@pytest.mark.asyncio
async def test_reopen_restores_pendiente_and_records_actor_and_timestamp(env):
    _app, session, order, asesor, admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    await service.cancel_order(order.id, "Motivo", asesor.user_id)
    reopened = await service.reopen_order(order.id, admin.user_id)
    assert reopened.status == ServiceOrderStatus.PENDIENTE
    assert reopened.reopened_by_user_id == admin.user_id
    assert reopened.reopened_at is not None
    # The original cancellation stays visible as history.
    assert reopened.cancel_reason == "Motivo"
    assert reopened.cancelled_by_user_id == asesor.user_id


@pytest.mark.asyncio
async def test_reopen_a_non_cancelled_order_is_rejected(env):
    _app, session, order, _asesor, admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    with pytest.raises(ServiceOrderNotCancelledError):
        await service.reopen_order(order.id, admin.user_id)


@pytest.mark.asyncio
async def test_cancelling_again_after_reopening_clears_the_stale_reopen_trail(env):
    _app, session, order, asesor, admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    await service.cancel_order(order.id, "Primer motivo", asesor.user_id)
    await service.reopen_order(order.id, admin.user_id)
    order.status = ServiceOrderStatus.EN_PROGRESO
    session.commit()
    cancelled_again = await service.cancel_order(order.id, "Segundo motivo", asesor.user_id)
    assert cancelled_again.cancel_reason == "Segundo motivo"
    assert cancelled_again.reopened_by_user_id is None
    assert cancelled_again.reopened_at is None


@pytest.mark.asyncio
async def test_generic_update_cannot_be_used_to_cancel(env):
    _app, session, order, _asesor, _admin = env
    service = ServiceOrderService(AsyncAdapter(session))
    from app.core.exceptions import BadRequestError

    with pytest.raises(BadRequestError):
        await service.update_order(order.id, ServiceOrderUpdate(status=ServiceOrderStatus.CANCELADO))


# --- HTTP layer ------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_cancel_requires_a_reason(env):
    app, _session, order, asesor, _admin = env
    async with _client_as(app, asesor) as client:
        response = await client.post(f"/api/v1/service-orders/{order.id}/cancel", json={"reason": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_http_asesor_can_cancel_with_a_reason(env):
    app, _session, order, asesor, _admin = env
    async with _client_as(app, asesor) as client:
        response = await client.post(
            f"/api/v1/service-orders/{order.id}/cancel", json={"reason": "El cliente desistió."}
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelado"
    assert body["cancel_reason"] == "El cliente desistió."
    assert body["cancelled_by_user_id"] == str(asesor.user_id)


@pytest.mark.asyncio
async def test_http_patch_status_cancelado_is_blocked(env):
    app, _session, order, asesor, _admin = env
    async with _client_as(app, asesor) as client:
        response = await client.patch(
            f"/api/v1/service-orders/{order.id}", json={"status": "cancelado"}
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_http_asesor_cannot_reopen(env):
    app, session, order, asesor, _admin = env
    order.status = ServiceOrderStatus.CANCELADO
    session.commit()
    async with _client_as(app, asesor) as client:
        response = await client.post(f"/api/v1/service-orders/{order.id}/reopen")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_http_admin_can_reopen(env):
    app, session, order, _asesor, admin = env
    order.status = ServiceOrderStatus.CANCELADO
    session.commit()
    async with _client_as(app, admin) as client:
        response = await client.post(f"/api/v1/service-orders/{order.id}/reopen")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pendiente"
    assert body["reopened_by_user_id"] == str(admin.user_id)
