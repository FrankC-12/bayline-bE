import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_part_sales_fifo import AsyncAdapter

import app.core.models_registry  # noqa: F401
from app.core.database import Base
from app.core.exceptions import DomainError
from app.core.security import hash_password
from app.modules.auth.exceptions import InvalidCredentialsError, InvalidRefreshTokenError
from app.modules.auth.models import RefreshSession
from app.modules.auth.schemas import CurrentUser
from app.modules.auth.service import AuthService
from app.modules.notifications.models import Device, Notification
from app.modules.notifications.service import deliver_batch
from app.modules.roles.enums import RoleScope
from app.modules.roles.models import Role
from app.modules.users.enums import UserStatus
from app.modules.users.models import User


@pytest.fixture
def auth_env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        role = Role(name="Asesor", slug="asesor", scope=RoleScope.FILIAL)
        session.add(role)
        session.flush()
        user = User(
            full_name="Asesor mobile",
            email="mobile@example.com",
            password_hash=hash_password("Mobile123"),
            status=UserStatus.ACTIVO,
            role_id=role.id,
        )
        session.add(user)
        session.commit()
        yield AuthService(AsyncAdapter(session)), session, user


@pytest.mark.asyncio
async def test_refresh_rotates_once_and_logout_revokes_its_replacement(auth_env):
    service, session, user = auth_env
    initial = await service.login(user.email, "Mobile123")
    replacement = await service.refresh(initial.refresh_token)
    assert replacement.refresh_token != initial.refresh_token
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh(initial.refresh_token)
    await service.logout(replacement.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh(replacement.refresh_token)
    rows = session.scalars(select(RefreshSession)).all()
    assert all(row.revoked_at for row in rows)
    assert all(
        row.token_hash not in (initial.refresh_token, replacement.refresh_token) for row in rows
    )


@pytest.mark.asyncio
async def test_login_errors_do_not_disclose_whether_the_email_exists(auth_env):
    service, _session, user = auth_env
    messages = []
    for email in (user.email, "unknown@example.com"):
        with pytest.raises(InvalidCredentialsError) as error:
            await service.login(email, "wrong")
        messages.append(error.value.message)
    assert messages[0] == messages[1]


@pytest.mark.asyncio
async def test_login_limits_email_even_if_source_ip_changes(auth_env):
    service, _session, user = auth_env
    for i in range(10):
        await service.check_login_budget(user.email, f"10.0.0.{i}")
    with pytest.raises(DomainError) as error:
        await service.check_login_budget(user.email.upper(), "10.1.2.3")
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_login_limits_ip_even_when_email_changes(auth_env):
    service, _session, _user = auth_env
    for i in range(100):
        await service.check_login_budget(f"{i}@example.com", "10.0.0.1")
    with pytest.raises(DomainError) as error:
        await service.check_login_budget("new@example.com", "10.0.0.1")
    assert error.value.status_code == 429


@pytest.mark.asyncio
async def test_push_retries_without_resending_to_devices_already_delivered(auth_env):
    service, session, user = auth_env
    devices = [
        Device(user_id=user.id, token_hash=str(i), token=f"test-device-{i}", platform="android")
        for i in range(2)
    ]
    n = Notification(
        user_id=user.id,
        event_key="tarea:1",
        kind="tarea_cerrada",
        title="Tarea cerrada",
        body="ODS-1",
        data={},
    )
    session.add_all([*devices, n])
    session.commit()
    sent = []

    def first_sender(device, notification):
        if device.id == devices[1].id:
            raise RuntimeError("Network unavailable")
        sent.append(device.id)

    await deliver_batch(service.db, first_sender)
    assert sent == [devices[0].id]
    assert n.sent_at is None
    n.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    await deliver_batch(service.db, lambda device, notification: sent.append(device.id))
    assert sent == [devices[0].id, devices[1].id]
    assert n.sent_at is not None


@pytest.mark.asyncio
async def test_device_registration_is_idempotent_and_cannot_delete_another_users_device(auth_env):
    from app.core.exceptions import NotFoundError
    from app.modules.notifications.router import DeviceInput, register_device, unregister_device

    service, _session, user = auth_env
    actor = CurrentUser(
        user_id=user.id,
        email=user.email,
        role_id=user.role_id,
        role_slug="asesor",
        scope=RoleScope.FILIAL,
        holding_id=None,
        filial_id=None,
    )
    payload = DeviceInput(
        token="a-realistic-test-token-with-more-than-twenty-characters", platform="ios"
    )
    first = await register_device(payload, actor, service.db)
    second = await register_device(payload, actor, service.db)
    assert first["id"] == second["id"]
    stranger = actor.model_copy(update={"user_id": uuid.uuid4()})
    with pytest.raises(NotFoundError):
        await unregister_device(first["id"], stranger, service.db)
    await unregister_device(first["id"], actor, service.db)


@pytest.mark.asyncio
async def test_outbox_is_rolled_back_with_the_workshop_transaction(auth_env):
    from types import SimpleNamespace

    from app.modules.notifications.service import queue_order_event

    service, session, user = auth_env
    order = SimpleNamespace(
        id=uuid.uuid4(), code="ODS-1", filial_id=uuid.uuid4(), advisor_user_id=user.id
    )
    await queue_order_event(service.db, order, "orden_lista", order.id)
    session.rollback()
    assert session.scalars(select(Notification)).all() == []


@pytest.mark.asyncio
async def test_every_event_is_persisted_once_and_has_the_correct_recipient(auth_env):
    from types import SimpleNamespace

    from app.modules.notifications.service import TITLES, queue_order_event

    service, session, user = auth_env
    order = SimpleNamespace(
        id=uuid.uuid4(), code="ODS-1", filial_id=uuid.uuid4(), advisor_user_id=user.id
    )
    for kind in TITLES:
        await queue_order_event(service.db, order, kind, order.id, [user.id])
        await queue_order_event(service.db, order, kind, order.id, [user.id])
    session.commit()
    rows = session.scalars(select(Notification)).all()
    assert {row.kind for row in rows} == set(TITLES)
    assert len(rows) == len(TITLES)
    assert all(row.user_id == user.id for row in rows)


def test_push_message_carries_the_inbox_data_and_plays_a_sound_on_ios():
    from types import SimpleNamespace

    from app.modules.notifications.service import build_message

    device = SimpleNamespace(token="test-device-token")
    notification = SimpleNamespace(
        id=uuid.uuid4(),
        title="Nueva orden asignada",
        body="ODS-1: nueva orden asignada.",
        data={"event": "tecnico_asignado", "service_order_id": "order-1"},
    )
    message = build_message(device, notification)
    assert message.token == "test-device-token"
    assert message.notification.title == "Nueva orden asignada"
    assert message.data == {
        "event": "tecnico_asignado",
        "service_order_id": "order-1",
        "notification_id": str(notification.id),
    }
    assert message.apns.payload.aps.sound == "default"


@pytest.mark.asyncio
async def test_push_data_names_the_order(auth_env):
    from types import SimpleNamespace

    from app.modules.notifications.service import queue_order_event

    service, session, user = auth_env
    order = SimpleNamespace(
        id=uuid.uuid4(), code="ODS-1", filial_id=uuid.uuid4(), advisor_user_id=user.id
    )
    await queue_order_event(service.db, order, "orden_lista", order.id)
    session.commit()
    row = session.scalars(select(Notification)).one()
    assert row.data["service_order_code"] == "ODS-1"
