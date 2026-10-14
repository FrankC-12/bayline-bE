"""Date-based JSON logs with request context and no request bodies or credentials."""

import copy
import json
import logging
import logging.handlers
import queue
import sys
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

request_context: ContextVar[dict | None] = ContextVar("audit_request_context", default=None)
audit_logger = logging.getLogger("app.audit")
error_logger = logging.getLogger("app.errors")
_database_registered = False


def set_actor(user_id, filial_id=None, holding_id=None):
    # Context is a mutable per-request dictionary: sync authentication runs in
    # a worker thread with a copied ContextVar, but shares this dictionary.
    context = request_context.get()
    if context is not None:
        context.update(
            user_id=str(user_id),
            filial_id=str(filial_id) if filial_id else None,
            holding_id=str(holding_id) if holding_id else None,
        )


def record_event(logger, event_name, **fields):
    logger.log(
        logging.ERROR if logger is error_logger else logging.INFO,
        event_name,
        extra={"event_data": {"event": event_name, **fields}},
    )


def record_error(event_name, **fields):
    context = request_context.get()
    if context is not None:
        context["error_logged"] = True
    record_event(error_logger, event_name, **fields)


class ContextQueueHandler(logging.handlers.QueueHandler):
    def prepare(self, record):
        # Never format arbitrary exception messages (SQL drivers include bound
        # values). Snapshot context before crossing to the logging thread.
        result = copy.copy(record)
        context = request_context.get() or {}
        result.audit_context = {
            key: context.get(key)
            for key in ("request_id", "user_id", "filial_id", "holding_id", "method", "route")
        }
        if result.exc_info:
            result.exception_type = result.exc_info[0].__name__
            result.stack = [
                {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                for frame in traceback.extract_tb(result.exc_info[2])
            ]
        result.exc_info = result.exc_text = None
        result.args = None
        return result


class DailyJsonHandler(logging.Handler):
    def __init__(self, directory, timezone="America/Caracas"):
        super().__init__()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.timezone = ZoneInfo(timezone)

    def emit(self, record):
        if record.name in {"app.core.request_tracking", "app.core.exception_handlers"}:
            return  # Dedicated structured events already cover these records.
        timestamp = datetime.fromtimestamp(record.created, self.timezone)
        if record.name == "app.audit":
            category = "audit"
        elif record.levelno >= logging.WARNING:
            category = "errors"
        else:
            category = "application"
        context = getattr(record, "audit_context", {})
        payload = {
            "timestamp": timestamp.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            **context,
            **getattr(record, "event_data", {"event": "application_log"}),
        }
        if hasattr(record, "exception_type"):
            payload.update(exception_type=record.exception_type, stack=record.stack)
        try:
            folder = self.directory / timestamp.date().isoformat()
            folder.mkdir(parents=True, exist_ok=True)
            # Append and close per record; works across midnight and restarts.
            with (folder / f"{category}.jsonl").open("a", encoding="utf-8") as target:
                target.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except OSError:
            # Logging failure must not roll back a completed operation or print
            # the original LogRecord, which could contain sensitive arguments.
            print(
                "Bayline: no se pudo escribir la bitácora; revisar disco/permisos.", file=sys.stderr
            )


class AuditLogRuntime:
    def __init__(self, directory, timezone="America/Caracas"):
        self.writer = DailyJsonHandler(directory, timezone)
        self.handler = ContextQueueHandler(queue.SimpleQueue())
        self.listener = logging.handlers.QueueListener(self.handler.queue, self.writer)
        self.logger = logging.getLogger("app")
        self.previous_level = self.logger.level

    def start(self):
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self.handler)
        self.listener.start()
        return self

    def stop(self):
        self.logger.removeHandler(self.handler)
        self.listener.stop()  # Drain queued records on graceful shutdown.
        self.logger.setLevel(self.previous_level)
        self.writer.close()


def register_database_audit():
    global _database_registered
    if _database_registered:
        return
    _database_registered = True

    @event.listens_for(Session, "after_flush")
    def collect(session, flush_context):
        transaction = session.get_nested_transaction() or session.get_transaction()
        changes = session.info.setdefault("audit_changes", {}).setdefault(transaction, [])
        for operation, objects in (
            ("create", session.new),
            ("update", session.dirty),
            ("delete", session.deleted),
        ):
            for obj in objects:
                if operation == "update" and not session.is_modified(
                    obj, include_collections=False
                ):
                    continue
                state = inspect(obj)
                identifiers = {
                    column.key: str(state.dict[column.key])
                    for column in state.mapper.primary_key
                    if isinstance(state.dict.get(column.key), (uuid.UUID, int))
                }
                fields = [
                    attribute.key
                    for attribute in state.mapper.column_attrs
                    if state.attrs[attribute.key].history.has_changes()
                ]
                changes.append(
                    {
                        "operation": operation,
                        "entity": state.mapper.local_table.name,
                        "resource_ids": identifiers,
                        "changed_fields": fields,
                    }
                )

    @event.listens_for(Session, "do_orm_execute", retval=True)
    def bulk_change(execution):
        if not (execution.is_insert or execution.is_update or execution.is_delete):
            return None
        result = execution.invoke_statement()
        session = execution.session
        transaction = session.get_nested_transaction() or session.get_transaction()
        mapper = execution.bind_mapper
        table = (
            mapper.local_table
            if mapper is not None
            else getattr(execution.statement, "table", None)
        )
        if table is not None:
            changes = session.info.setdefault("audit_changes", {}).setdefault(transaction, [])
            operation = (
                "bulk_insert"
                if execution.is_insert
                else ("bulk_update" if execution.is_update else "bulk_delete")
            )
            changes.append(
                {
                    "operation": operation,
                    "entity": table.name,
                    "resource_ids": {},
                    "changed_fields": [],
                    "affected_rows": getattr(result, "rowcount", None),
                }
            )
        return result

    @event.listens_for(Session, "after_commit")
    def committed(session):
        pending = session.info.get("audit_changes", {})
        nested = session.get_nested_transaction()
        if nested:
            pending.setdefault(nested.parent, []).extend(pending.pop(nested, []))
            return
        for changes in pending.values():
            for change in changes:
                record_event(audit_logger, "database_change", **change)
        session.info.pop("audit_changes", None)

    @event.listens_for(Session, "after_rollback")
    def rolled_back(session):
        nested = session.get_nested_transaction()
        if nested:
            session.info.get("audit_changes", {}).pop(nested, None)
        else:
            session.info.pop("audit_changes", None)
