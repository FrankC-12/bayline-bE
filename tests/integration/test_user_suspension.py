"""Suspending another administrator revokes web/mobile sessions permanently."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import get_db
from app.core.exception_handlers import register_exception_handlers
from app.core.security import hash_password
from app.modules.auth.router import router as auth_router
from app.modules.auth.service import AuthService
from app.modules.roles.enums import RoleScope
from app.modules.roles.models import Role
from app.modules.users.enums import UserStatus
from app.modules.users.models import User
from app.modules.users.router import router as users_router


@pytest.mark.asyncio
async def test_superadmin_suspension_revokes_access_and_refresh(billing_db):
    ctx = billing_db
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(users_router, prefix="/api/v1")
    register_exception_handlers(app)
    async with ctx.sessions() as db:
        role = (
            await db.execute(select(Role).where(Role.slug == "filial-admin"))
        ).scalar_one_or_none()
        if role is None:
            role = Role(name="Super administrador", slug="filial-admin", scope=RoleScope.FILIAL)
            db.add(role)
            await db.flush()
        accounts = [
            User(
                full_name=f"Admin {i}",
                email=f"{uuid.uuid4().hex}@example.com",
                role_id=role.id,
                filial_id=ctx.filial_id,
                status=UserStatus.ACTIVO,
                password_hash=hash_password("Suspend123"),
            )
            for i in range(2)
        ]
        db.add_all(accounts)
        await db.commit()
        actor, target = accounts
        auth = AuthService(db)
        actor_tokens = await auth.login(actor.email, "Suspend123")
        target_tokens = await auth.login(target.email, "Suspend123")

        async def database():
            yield db

        app.dependency_overrides[get_db] = database
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {actor_tokens.access_token}"}
            response = await client.patch(
                f"/api/v1/users/{target.id}", headers=headers, json={"status": "inactivo"}
            )
            assert response.status_code == 200, response.text
            # Both transports reject the still-unexpired access token.
            for kwargs in (
                {"headers": {"Authorization": f"Bearer {target_tokens.access_token}"}},
                {"cookies": {"bayline_access_token": target_tokens.access_token}},
            ):
                # Use the actual cookie name from the auth module below.
                if "cookies" in kwargs:
                    from app.modules.auth.cookies import ACCESS_COOKIE

                    kwargs = {"cookies": {ACCESS_COOKIE: target_tokens.access_token}}
                response = await client.get("/api/v1/auth/me", **kwargs)
                assert response.status_code == 401
                assert response.json()["errorCode"] == "inactive_user"
            response = await client.post(
                "/api/v1/auth/login", json={"email": target.email, "password": "Suspend123"}
            )
            assert response.status_code == 401
            assert "suspendido" in response.json()["message"]
            response = await client.patch(
                f"/api/v1/users/{actor.id}", headers=headers, json={"status": "inactivo"}
            )
            assert response.status_code == 400
            response = await client.patch(
                f"/api/v1/users/{target.id}", headers=headers, json={"status": "activo"}
            )
            assert response.status_code == 200
            response = await client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {target_tokens.access_token}"}
            )
            assert response.status_code == 401
            response = await client.post(
                "/api/v1/auth/refresh", json={"refresh_token": target_tokens.refresh_token}
            )
            assert response.status_code == 401
            # Re-enabled users must authenticate again with their password.
            response = await client.post(
                "/api/v1/auth/login", json={"email": target.email, "password": "Suspend123"}
            )
            assert response.status_code == 200
