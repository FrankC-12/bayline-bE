"""Atomic operation receipts: same key/payload replays, changed data conflicts."""

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit_logging import audit_logger, record_event
from app.core.database import Base
from app.core.exceptions import ConflictError


class OperationReceipt(Base):
    __tablename__ = "operation_receipts"
    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    filial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("filiales.id", ondelete="CASCADE"))
    operation: Mapped[str] = mapped_column(String(60))
    payload_hash: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    response: Mapped[dict | list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def payload_hash(payload, resource_id=None):
    data = {
        "payload": payload.model_dump(mode="json", exclude={"request_id"}),
        "resource_id": str(resource_id) if resource_id else None,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def replay_operation(db, payload, filial_id, operation, actor_id, *, resource_id=None):
    if payload.request_id is None:
        return None
    if db.get_bind().dialect.name == "postgresql":
        # Same request key is serialized across resources and operation types.
        key = int.from_bytes(payload.request_id.bytes[:8], "big", signed=True)
        await db.execute(select(func.pg_advisory_xact_lock(key)))
    receipt = await db.get(OperationReceipt, payload.request_id, populate_existing=True)
    if receipt is None:
        return None
    if (
        receipt.filial_id != filial_id
        or receipt.operation != operation
        or receipt.actor_id != actor_id
        or receipt.payload_hash != payload_hash(payload, resource_id)
    ):
        raise ConflictError(
            "Este identificador corresponde a otra operación o a otros datos.",
            error_code="operation_request_conflict",
        )
    record_event(
        audit_logger,
        "operation_replayed",
        operation=operation,
        operation_request_id=str(payload.request_id),
    )
    return receipt.response


def remember_operation(db, payload, filial_id, operation, actor_id, response, *, resource_id=None):
    if payload.request_id is not None:
        db.add(
            OperationReceipt(
                request_id=payload.request_id,
                filial_id=filial_id,
                operation=operation,
                actor_id=actor_id,
                payload_hash=payload_hash(payload, resource_id),
                response=response,
            )
        )
