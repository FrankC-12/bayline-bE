"""Write a safe audit row in the business transaction; rollback removes both."""

import uuid

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.core.audit_logging import request_context
from app.modules.filiales.models import Filial
from app.modules.holdings.control_models import HoldingAuditEvent

_registered = False


def register_holding_audit():
    global _registered
    if _registered:
        return
    _registered = True

    @event.listens_for(Session, "after_flush")
    def persist(session, flush_context):
        context = request_context.get() or {}
        if not context.get("user_id"):
            return
        connection = session.connection()
        for action, objects in (
            ("create", session.new),
            ("update", session.dirty),
            ("delete", session.deleted),
        ):
            for obj in objects:
                state = inspect(obj)
                entity = state.mapper.local_table.name
                if entity in {
                    "holding_audit_events",
                    "auth_login_buckets",
                    "auth_refresh_sessions",
                    "api_rate_buckets",
                    "devices",
                    "notifications",
                }:
                    continue
                if action == "update" and not session.is_modified(obj, include_collections=False):
                    continue
                filial = getattr(obj, "filial_id", None) or context.get("filial_id")
                holding = getattr(obj, "holding_id", None) or context.get("holding_id")
                if filial:
                    filial = uuid.UUID(str(filial))
                    holding = connection.execute(
                        select(Filial.holding_id).where(Filial.id == filial)
                    ).scalar()
                if not holding:
                    continue
                fields = [
                    a.key
                    for a in state.mapper.column_attrs
                    if state.attrs[a.key].history.has_changes()
                    and not any(s in a.key for s in ("password", "token", "secret"))
                ]
                connection.execute(
                    HoldingAuditEvent.__table__.insert().values(
                        id=uuid.uuid4(),
                        holding_id=uuid.UUID(str(holding)),
                        filial_id=filial,
                        user_id=uuid.UUID(context["user_id"]),
                        action=action,
                        entity=entity,
                        resource_id=str(getattr(obj, "id", "")) or None,
                        changed_fields=fields,
                    )
                )
