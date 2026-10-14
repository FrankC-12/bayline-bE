"""Attach a server-generated correlation ID without logging request bodies or credentials."""

import logging
import time
import uuid

from app.core.audit_logging import audit_logger, record_error, record_event, request_context

logger = logging.getLogger(__name__)


class RequestTrackingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        context = scope["state"]
        context.update(method=scope["method"], route="unmatched")
        context_token = request_context.set(context)
        failed = False
        started = time.monotonic()
        status = 500

        async def tracked_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                message["headers"].append((b"x-request-id", request_id.encode("ascii")))
            await send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            failed = True
            if not context.get("error_logged"):
                import traceback

                record_error(
                    "unexpected_error",
                    exception_type=type(exc).__name__,
                    route=getattr(scope.get("route"), "path", "unmatched"),
                    stack=[
                        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
                        for frame in traceback.extract_tb(exc.__traceback__)
                    ],
                )
            raise
        finally:
            route = scope.get("route")
            context["route"] = getattr(route, "path", "unmatched")
            resources = {}
            for name, value in scope.get("path_params", {}).items():
                try:
                    resources[name] = str(uuid.UUID(str(value)))
                except ValueError:
                    if isinstance(value, int):
                        resources[name] = value
            record_event(
                audit_logger,
                "http_request",
                status=status,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
                resource_ids=resources,
                outcome="failed" if failed or status >= 400 else "success",
            )
            if status >= 400 and not context.get("error_logged"):
                record_error("http_error", status=status)

            logger.info(
                "http_request request_id=%s method=%s route=%s status=%s duration_ms=%.1f",
                request_id,
                scope["method"],
                getattr(route, "path", "unmatched"),
                status,
                (time.monotonic() - started) * 1000,
            )
            request_context.reset(context_token)
