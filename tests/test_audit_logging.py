import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.core.audit_logging import (
    AuditLogRuntime,
    DailyJsonHandler,
    register_database_audit,
    request_context,
    set_actor,
)
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import BadRequestError
from app.core.request_tracking import RequestTrackingMiddleware
from app.core.security import create_access_token, decode_access_token
from app.core.database import get_db
from auth_test_support import account_database
from app.core.telemetry import router as telemetry_router
from app.modules.auth.dependencies import get_current_user
from app.modules.auth.schemas import CurrentUser


def logs(directory, category):
    return [
        json.loads(line)
        for path in sorted(directory.glob(f"*/{category}.jsonl"))
        for line in path.read_text().splitlines()
    ]


def test_daily_files_use_caracas_date_and_append_after_restart(tmp_path):
    for at in (
        datetime(2026, 10, 14, 2, tzinfo=UTC),
        datetime(2026, 10, 14, 4, tzinfo=UTC),
        datetime(2026, 10, 14, 3, tzinfo=UTC),
    ):
        handler = DailyJsonHandler(tmp_path)
        record = logging.LogRecord("app.audit", logging.INFO, __file__, 1, "ignored", (), None)
        record.created = at.timestamp()
        record.event_data = {"event": "test"}
        handler.handle(record)
        handler.close()
    first = tmp_path / "2026-10-13" / "audit.jsonl"
    second = tmp_path / "2026-10-14" / "audit.jsonl"
    assert len(first.read_text().splitlines()) == 2
    assert len(second.read_text().splitlines()) == 1
    assert json.loads(first.read_text().splitlines()[0])["timestamp"].endswith("-04:00")


@pytest.mark.asyncio
async def test_http_audit_correlates_actor_failures_and_redacts_sensitive_data(tmp_path):
    runtime = AuditLogRuntime(tmp_path).start()
    app = FastAPI()
    app.add_middleware(RequestTrackingMiddleware)
    app.include_router(telemetry_router)
    register_exception_handlers(app)
    user_id, filial_id, resource_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token = create_access_token(
        {
            "sub": str(user_id),
            "email": "private-email@example.com",
            "role_id": str(uuid.uuid4()),
            "role_slug": "filial-admin",
            "scope": "filial",
            "filial_id": str(filial_id),
            "holding_id": None,
        }
    )

    account_db = account_database(decode_access_token(token))
    app.dependency_overrides[get_db] = lambda: account_db

    @app.post("/audit/{resource_id}")
    async def action(
        resource_id: uuid.UUID,
        payload: dict,
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ):
        if payload.get("failure") == "domain":
            raise BadRequestError("secret-domain-message", error_code="invalid_action")
        if payload.get("failure") == "unexpected":
            raise RuntimeError("secret-exception-message")
        return {"ok": True}

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as http:
            ids = []
            for failure, status in ((None, 200), ("domain", 400), ("unexpected", 500)):
                response = await http.post(
                    f"/audit/{resource_id}?token=secret-query",
                    json={"password": "secret-body", "failure": failure},
                    headers={"Authorization": f"Bearer {token}", "X-Request-ID": "forged"},
                )
                assert response.status_code == status
                ids.append(response.headers["x-request-id"])
            denied = await http.post(f"/audit/{resource_id}", json={})
            assert denied.status_code == 401
            report = {
                "platform": "web",
                "kind": "unhandled_rejection",
                "error_type": "TypeError",
                "area": "inventory",
            }
            assert (await http.post("/telemetry/errors", json=report)).status_code == 401
            headers = {"Authorization": f"Bearer {token}"}
            assert (
                await http.post(
                    "/telemetry/errors",
                    json={**report, "message": "secret-report"},
                    headers=headers,
                )
            ).status_code == 422
            assert (
                await http.post("/telemetry/errors", json=report, headers=headers)
            ).status_code == 204
    finally:
        runtime.stop()
    audit = [
        entry
        for entry in logs(tmp_path, "audit")
        if entry["event"] == "http_request" and entry["route"] == "/audit/{resource_id}"
    ]
    assert len(audit) == 4
    assert [entry["status"] for entry in audit] == [200, 400, 500, 401]
    for entry, identifier in zip(audit, ids, strict=False):
        assert entry["request_id"] == identifier
        assert entry["user_id"] == str(user_id)
        assert entry["filial_id"] == str(filial_id)
        assert entry["resource_ids"]["resource_id"] == str(resource_id)
    assert audit[-1]["user_id"] is None
    errors = logs(tmp_path, "errors")
    assert any(
        entry.get("exception_type") == "RuntimeError" and entry["request_id"] == ids[2]
        for entry in errors
    )
    assert any(entry.get("error_code") == "invalid_action" for entry in errors)
    client_error = next(entry for entry in errors if entry["event"] == "client_error")
    assert client_error["user_id"] == str(user_id)
    assert client_error["error_type"] == "TypeError"
    contents = "\n".join(path.read_text() for path in tmp_path.glob("*/*.jsonl"))
    for secret in (
        "secret-body",
        "secret-query",
        "secret-domain-message",
        "secret-exception-message",
        "private-email",
        token,
        "forged",
    ):
        assert secret not in contents
    assert request_context.get() is None


class AuditTestBase(DeclarativeBase):
    pass


class Entry(AuditTestBase):
    __tablename__ = "audit_test_entries"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    description: Mapped[str] = mapped_column(String)


def test_database_audit_only_logs_commits_and_honors_savepoints(tmp_path):
    register_database_audit()
    runtime = AuditLogRuntime(tmp_path).start()
    engine = create_engine("sqlite://")
    AuditTestBase.metadata.create_all(engine)
    token = request_context.set({"request_id": "test-request"})
    user_id = uuid.uuid4()
    set_actor(user_id)
    try:
        with Session(engine, expire_on_commit=False) as db:
            entry = Entry(description="secret-original")
            db.add(entry)
            db.commit()
            identifier = entry.id
            entry.description = "secret-updated"
            db.commit()
            entry.description = "secret-rollback"
            db.flush()
            db.rollback()
            # Committing a savepoint must not log a change if its outer transaction rolls back.
            with db.begin():
                nested = db.begin_nested()
                db.add(Entry(description="secret-inner"))
                db.flush()
                nested.commit()
                db.rollback()
            # Rolling back a savepoint must preserve earlier changes in the outer transaction.
            with db.begin():
                entry.description = "secret-kept"
                db.flush()
                nested = db.begin_nested()
                db.add(Entry(description="secret-discarded"))
                db.flush()
                nested.rollback()
            db.delete(entry)
            db.commit()
    finally:
        request_context.reset(token)
        runtime.stop()
        engine.dispose()
    changes = [entry for entry in logs(tmp_path, "audit") if entry["event"] == "database_change"]
    assert [entry["operation"] for entry in changes] == ["create", "update", "update", "delete"]
    assert all(entry["resource_ids"] == {"id": str(identifier)} for entry in changes)
    assert all(
        entry["request_id"] == "test-request" and entry["user_id"] == str(user_id)
        for entry in changes
    )
    assert "description" in changes[1]["changed_fields"]
    assert "secret-" not in str(changes)


@pytest.mark.asyncio
async def test_application_lifecycle_starts_and_drains_daily_logs(tmp_path, monkeypatch):
    import asyncio

    import app.main as main

    async def idle_background_job():
        await asyncio.Event().wait()

    monkeypatch.setattr(main.settings, "logs_dir", str(tmp_path))
    monkeypatch.setattr(main, "_refresh_bcv_rate_loop", idle_background_job)
    async with main.lifespan(main.app):
        pass
    records = logs(tmp_path, "audit")
    assert [entry["event"] for entry in records] == ["application_started", "application_stopped"]
