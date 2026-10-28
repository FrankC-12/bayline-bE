"""Real PostgreSQL OTP consumption, account/tenant suspension and transport isolation."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.core.security import hash_password, verify_password
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.auth.recovery_router import router as recovery_router
from app.modules.auth.models import RecoveryChallenge
from app.modules.auth.service import AuthService
from app.modules.auth import otp_delivery
from app.modules.filiales.models import Filial
from app.modules.holdings.service import HoldingService
from app.modules.users.service import UserService
from app.modules.users.schemas import UserUpdate
from app.modules.roles.enums import RoleScope
from app.modules.roles.models import Role
from app.modules.users.models import User
from app.modules.users.enums import UserStatus


@pytest_asyncio.fixture
async def recovery_env(billing_db, monkeypatch):
    ctx = billing_db
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    app.include_router(recovery_router, prefix="/api/v1")
    register_exception_handlers(app)
    sent = []

    async def send(channel, destination, code):
        sent.append((channel, destination, code))

    monkeypatch.setattr(otp_delivery, "channel_available", lambda channel: True)
    monkeypatch.setattr(otp_delivery, "send_otp", send)
    async with ctx.sessions() as db:
        filial = await db.get(Filial, ctx.filial_id)
        role = (
            await db.execute(select(Role).where(Role.slug == "holding-admin"))
        ).scalar_one_or_none()
        if role is None:
            role = Role(name="Holding", slug="holding-admin", scope=RoleScope.HOLDING)
            db.add(role)
        employee = Role(name="Employee", slug=uuid.uuid4().hex, scope=RoleScope.FILIAL)
        db.add(employee)
        await db.flush()
        owner = User(
            full_name="Holding owner",
            email=f"{uuid.uuid4().hex}@example.com",
            role_id=role.id,
            holding_id=filial.holding_id,
            status=UserStatus.ACTIVO,
            password_hash=hash_password("Original123"),
        )
        worker = User(
            full_name="Branch user",
            email=f"{uuid.uuid4().hex}@example.com",
            role_id=employee.id,
            filial_id=filial.id,
            status=UserStatus.ACTIVO,
            password_hash=hash_password("Original123"),
        )
        db.add_all([owner, worker])
        await db.commit()
        tokens = await AuthService(db).login(owner.email, "Original123")
        worker_tokens = await AuthService(db).login(worker.email, "Original123")

        async def database():
            yield db

        app.dependency_overrides[get_db] = database
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(
                db=db,
                client=client,
                owner=owner,
                worker=worker,
                filial=filial,
                tokens=tokens,
                worker_tokens=worker_tokens,
                sent=sent,
                sessions=ctx.sessions,
            )


@pytest.mark.asyncio
async def test_holding_email_sms_reset_and_verified_phone(recovery_env):
    e = recovery_env
    c = e.client
    response = await c.post(
        "/api/v1/auth/password-recovery/options", json={"email": e.worker.email}
    )
    assert response.json()["action"] == "contact_admin"
    assert "sucursal" in response.json()["message"]
    response = await c.post(
        "/api/v1/auth/password-recovery/request", json={"email": e.worker.email, "channel": "email"}
    )
    assert response.status_code == 400 and not e.sent
    options = (
        await c.post("/api/v1/auth/password-recovery/options", json={"email": e.owner.email})
    ).json()
    assert options["action"] == "otp" and options["channels"][0]["available"]
    assert not options["channels"][1]["available"]
    headers = {"Authorization": f"Bearer {e.tokens.access_token}"}
    response = await c.post(
        "/api/v1/auth/recovery-phone/request",
        headers=headers,
        json={"phone": "+584121234567", "current_password": "Original123"},
    )
    assert response.status_code == 200, response.text
    ticket = response.json()
    assert "code" not in ticket and "+584121234567" not in response.text
    code = e.sent[-1][2]
    # Enrollment codes cannot reset passwords.
    response = await c.post(
        "/api/v1/auth/password-recovery/reset",
        json={"challenge_id": ticket["challenge_id"], "code": code, "new_password": "Replaced123"},
    )
    assert response.status_code == 400
    response = await c.post(
        "/api/v1/auth/recovery-phone/verify",
        headers=headers,
        json={"challenge_id": ticket["challenge_id"], "code": code},
    )
    assert response.status_code == 204
    options = (
        await c.post("/api/v1/auth/password-recovery/options", json={"email": e.owner.email})
    ).json()
    assert options["channels"][1]["available"]
    response = await c.post(
        "/api/v1/auth/password-recovery/request", json={"email": e.owner.email, "channel": "sms"}
    )
    assert response.status_code == 200, response.text
    ticket = response.json()
    code = e.sent[-1][2]
    assert e.sent[-1][:2] == ("sms", "+584121234567")
    response = await c.post(
        "/api/v1/auth/password-recovery/reset",
        json={
            "challenge_id": ticket["challenge_id"],
            "code": code,
            "new_password": "NewPassword123",
        },
    )
    assert response.status_code == 204, response.text
    assert e.owner.status == UserStatus.ACTIVO
    assert verify_password("NewPassword123", e.owner.password_hash)
    assert (await c.get("/api/v1/auth/me", headers=headers)).status_code == 401
    assert (
        await c.post("/api/v1/auth/refresh", json={"refresh_token": e.tokens.refresh_token})
    ).status_code == 401
    response = await c.post(
        "/api/v1/auth/password-recovery/reset",
        json={"challenge_id": ticket["challenge_id"], "code": code, "new_password": "Another123"},
    )
    assert response.status_code == 400
    assert (
        await c.post(
            "/api/v1/auth/login", json={"email": e.owner.email, "password": "NewPassword123"}
        )
    ).status_code == 200


@pytest.mark.asyncio
async def test_otp_attempt_limit_and_resend_invalidate_old_code(recovery_env):
    e = recovery_env
    payload = {"email": e.owner.email, "channel": "email"}
    issued = (await e.client.post("/api/v1/auth/password-recovery/request", json=payload)).json()
    code = e.sent[-1][2]
    assert e.sent[-1][0] == "email" and e.sent[-1][1] == e.owner.email
    cooldown = await e.client.post("/api/v1/auth/password-recovery/request", json=payload)
    assert cooldown.status_code == 400 and cooldown.json()["errorCode"] == "otp_resend_wait"
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(5):
        response = await e.client.post(
            "/api/v1/auth/password-recovery/reset",
            json={
                "challenge_id": issued["challenge_id"],
                "code": wrong,
                "new_password": "Other12345",
            },
        )
        assert response.status_code == 400
    response = await e.client.post(
        "/api/v1/auth/password-recovery/reset",
        json={"challenge_id": issued["challenge_id"], "code": code, "new_password": "Other12345"},
    )
    assert response.status_code == 400
    challenge = await e.db.get(RecoveryChallenge, uuid.UUID(issued["challenge_id"]))
    assert challenge.attempts == 5 and challenge.code_hash != code
    challenge.expires_at = datetime.now(UTC) + timedelta(minutes=8)
    await e.db.commit()
    newer = (await e.client.post("/api/v1/auth/password-recovery/request", json=payload)).json()
    assert newer["challenge_id"] != issued["challenge_id"]
    assert challenge.consumed_at is not None
    new_challenge = await e.db.get(RecoveryChallenge, uuid.UUID(newer["challenge_id"]))
    new_challenge.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await e.db.commit()
    response = await e.client.post(
        "/api/v1/auth/password-recovery/reset",
        json={
            "challenge_id": newer["challenge_id"],
            "code": e.sent[-1][2],
            "new_password": "Other12345",
        },
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_holding_block_covers_branches_and_never_revives_sessions_or_codes(recovery_env):
    e = recovery_env
    issued = (
        await e.client.post(
            "/api/v1/auth/password-recovery/request",
            json={"email": e.owner.email, "channel": "email"},
        )
    ).json()
    code = e.sent[-1][2]
    holding_service = HoldingService(e.db)
    await holding_service.set_active_status(e.filial.holding_id, False)
    for user, tokens in [(e.owner, e.tokens), (e.worker, e.worker_tokens)]:
        response = await e.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens.access_token}"}
        )
        assert (
            response.status_code == 401 and response.json()["errorCode"] == "inactive_organization"
        )
        response = await e.client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": "Original123"}
        )
        assert response.status_code == 401
        options = (
            await e.client.post(
                "/api/v1/auth/password-recovery/options", json={"email": user.email}
            )
        ).json()
        assert options["action"] == "unavailable" and not options["channels"]
    response = await e.client.post(
        "/api/v1/auth/password-recovery/reset",
        json={"challenge_id": issued["challenge_id"], "code": code, "new_password": "Other12345"},
    )
    assert response.status_code == 401
    await holding_service.set_active_status(e.filial.holding_id, True)
    for tokens in [e.tokens, e.worker_tokens]:
        assert (
            await e.client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens.access_token}"}
            )
        ).status_code == 401
        assert (
            await e.client.post(
                "/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token}
            )
        ).status_code == 401
    response = await e.client.post(
        "/api/v1/auth/password-recovery/reset",
        json={"challenge_id": issued["challenge_id"], "code": code, "new_password": "Other12345"},
    )
    assert response.status_code == 400
    await UserService(e.db).update_user(e.owner.id, UserUpdate(status=UserStatus.INACTIVO))
    assert (
        await e.client.post("/api/v1/auth/password-recovery/options", json={"email": e.owner.email})
    ).json()["action"] == "unavailable"
    response = await e.client.post(
        "/api/v1/auth/password-recovery/request", json={"email": e.owner.email, "channel": "email"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_reset_code_is_single_use_under_concurrency(recovery_env):
    import asyncio
    from app.core.exceptions import DomainError
    from app.modules.auth.recovery import RecoveryService

    e = recovery_env
    issued = (
        await e.client.post(
            "/api/v1/auth/password-recovery/request",
            json={"email": e.owner.email, "channel": "email"},
        )
    ).json()
    code = e.sent[-1][2]

    async def attempt():
        async with e.sessions() as db:
            try:
                await RecoveryService(db).reset(
                    uuid.UUID(issued["challenge_id"]), code, "Concurrent123"
                )
                return "ok"
            except DomainError as error:
                return error.error_code

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == ["invalid_otp", "ok"]


@pytest.mark.asyncio
async def test_unconfigured_channels_and_unknown_users_never_send(recovery_env, monkeypatch):
    e = recovery_env
    monkeypatch.setattr(otp_delivery, "channel_available", lambda channel: False)
    options = (
        await e.client.post("/api/v1/auth/password-recovery/options", json={"email": e.owner.email})
    ).json()
    assert all(not channel["available"] for channel in options["channels"])
    response = await e.client.post(
        "/api/v1/auth/password-recovery/request", json={"email": e.owner.email, "channel": "email"}
    )
    assert response.status_code == 400 and not e.sent
    response = await e.client.post(
        "/api/v1/auth/password-recovery/options", json={"email": "missing@example.com"}
    )
    assert response.json()["action"] == "unavailable" and not e.sent


@pytest.mark.asyncio
async def test_branch_password_reset_requires_admin_identity_and_never_reactivates(recovery_env):
    e = recovery_env
    e.worker.holding_id = e.filial.holding_id
    await e.db.commit()
    path = f"/api/v1/users/{e.worker.id}/reset-password"
    payload = {"current_password": "Original123", "new_password": "AdminReset123"}
    response = await e.client.post(
        path, headers={"Authorization": f"Bearer {e.worker_tokens.access_token}"}, json=payload
    )
    assert response.status_code == 403
    headers = {"Authorization": f"Bearer {e.tokens.access_token}"}
    response = await e.client.post(
        path, headers=headers, json={**payload, "current_password": "incorrect"}
    )
    assert response.status_code == 400
    response = await e.client.post(path, headers=headers, json=payload)
    assert response.status_code == 204, response.text
    assert verify_password("AdminReset123", e.worker.password_hash)
    assert (
        await e.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {e.worker_tokens.access_token}"}
        )
    ).status_code == 401
    await UserService(e.db).update_user(e.worker.id, UserUpdate(status=UserStatus.INACTIVO))
    response = await e.client.post(path, headers=headers, json=payload)
    assert response.status_code == 400
    assert e.worker.status == UserStatus.INACTIVO
    assert (await e.client.get("/api/v1/auth/me", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_branch_suspension_does_not_disable_holding_but_revokes_branch_sessions(recovery_env):
    from app.modules.filiales.service import FilialService

    e = recovery_env
    service = FilialService(e.db)
    await service.set_active_status(e.filial.id, False)
    assert (
        await e.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {e.worker_tokens.access_token}"}
        )
    ).status_code == 401
    assert (
        await e.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {e.tokens.access_token}"}
        )
    ).status_code == 200
    await HoldingService(e.db).set_active_status(e.filial.holding_id, False)
    await HoldingService(e.db).set_active_status(e.filial.holding_id, True)
    assert e.filial.is_active is False
    await service.set_active_status(e.filial.id, True)
    assert e.worker.status == UserStatus.ACTIVO
    assert (
        await e.client.get(
            "/api/v1/auth/me", headers={"Authorization": f"Bearer {e.worker_tokens.access_token}"}
        )
    ).status_code == 401
