"""Shared fixed-minute request budgets, checked before reading request bodies."""

import asyncio
import hashlib
import logging
import math
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from sqlalchemy import DateTime, Integer, String, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, mapped_column
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal, Base
from app.core.security import decode_access_token
from app.modules.auth.cookies import ACCESS_COOKIE

logger = logging.getLogger(__name__)


class ApiRateBucket(Base):
    __tablename__ = "api_rate_buckets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


@dataclass(frozen=True)
class Budget:
    key: str
    limit: int


def request_budgets(request: Request, settings) -> list[Budget]:
    # Uvicorn resolves trusted proxy headers. Never trust X-Forwarded-For here.
    ip = request.client.host if request.client else "unknown"
    identity = f"ip:{ip}"
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
    if token and len(token) <= 8192:
        try:
            identity = f"user:{uuid.UUID(str(decode_access_token(token)['sub']))}"
        except (jwt.PyJWTError, KeyError, ValueError, TypeError):
            pass
    authenticated = identity.startswith("user:")
    budgets = [
        Budget(f"ip-total:{ip}", settings.rate_limit_ip_per_minute),
        Budget(
            f"total:{identity}",
            settings.rate_limit_user_per_minute
            if authenticated
            else settings.rate_limit_anonymous_per_minute,
        ),
    ]
    # Group by API resource, before routing and body parsing. IDs, query strings
    # and nested endpoints share the budget; no framework-private router APIs.
    resource = request.url.path[len(settings.api_v1_prefix) + 1 :].split("/", 1)[0]
    read = request.method in {"GET", "HEAD"}
    budgets.append(
        Budget(
            f"resource:{identity}:{request.method}:{resource}",
            settings.rate_limit_read_per_minute if read else settings.rate_limit_write_per_minute,
        )
    )
    if not read:
        budgets.append(Budget(f"write:{identity}", settings.rate_limit_write_per_minute))
    content_type = request.headers.get("content-type", "").lower()
    segments = set(request.url.path.lower().split("/"))
    if (
        content_type.startswith("multipart/")
        or bool(segments & {"export", "bulk"})
        or (not read and bool(segments & {"upload", "uploads", "photos", "annex"}))
    ):
        budgets.append(Budget(f"expensive:{identity}", settings.rate_limit_expensive_per_minute))
    if request.url.path.startswith(settings.api_v1_prefix + "/auth/"):
        # Independent of tokens, so logging out/refreshing cannot evade this budget.
        budgets.append(Budget(f"auth:{ip}", settings.rate_limit_auth_per_minute))
    return budgets


class PostgresRateLimiter:
    def __init__(self, sessions=AsyncSessionLocal, clock=time.time, blocked_cache_size=4096):
        self.sessions = sessions
        self.clock = clock
        self.next_cleanup = 0.0
        # Cache only committed rejections, never remaining capacity. Each worker
        # still consults PostgreSQL before allowing traffic from a shared budget.
        self.blocked_cache_size = max(0, blocked_cache_size)
        self.blocked: OrderedDict[tuple[str, int], float] = OrderedDict()

    async def check(self, budgets: list[Budget]) -> int | None:
        now = self.clock()
        window = int(now) // 60
        reset = (window + 1) * 60
        limits = {
            hashlib.sha256(f"{window}:{budget.key}".encode()).hexdigest(): budget.limit
            for budget in budgets
        }
        for key, limit in limits.items():
            cached = (key, limit)
            if self.blocked.get(cached, 0) > now:
                self.blocked.move_to_end(cached)
                return max(1, math.ceil(reset - now))
        values = [
            {"id": key, "attempts": 1, "expires_at": datetime.fromtimestamp(reset, UTC)}
            for key in sorted(limits)
        ]
        # Stable key order prevents deadlocks between requests sharing budgets.
        statement = insert(ApiRateBucket).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[ApiRateBucket.id],
            set_={"attempts": func.least(ApiRateBucket.attempts, 1000000) + 1},
        ).returning(ApiRateBucket.id, ApiRateBucket.attempts)
        async with self.sessions() as db:
            counts = (await db.execute(statement)).all()
            # Cleanup is bounded and skips rows locked by other instances.
            if now >= self.next_cleanup:
                self.next_cleanup = now + 60
                expired = (
                    select(ApiRateBucket.id)
                    .where(ApiRateBucket.expires_at < datetime.fromtimestamp(now, UTC))
                    .order_by(ApiRateBucket.expires_at)
                    .limit(1000)
                    .with_for_update(skip_locked=True)
                )
                await db.execute(delete(ApiRateBucket).where(ApiRateBucket.id.in_(expired)))
            await db.commit()
        exceeded = [key for key, count in counts if count > limits[key]]
        for key in exceeded:
            cached = (key, limits[key])
            self.blocked[cached] = reset
            self.blocked.move_to_end(cached)
            while len(self.blocked) > self.blocked_cache_size:
                self.blocked.popitem(last=False)
        if exceeded:
            return max(1, math.ceil(reset - now))
        return None


class RateLimitMiddleware:
    def __init__(self, app, limiter=None, settings=None):
        self.app = app
        self.limiter = limiter or PostgresRateLimiter()
        self.settings = settings or get_settings()

    async def __call__(self, scope, receive, send):
        settings = self.settings
        if scope["type"] != "http" or not settings.rate_limit_enabled:
            return await self.app(scope, receive, send)
        request = Request(scope)
        path = request.url.path
        if (
            not path.startswith(settings.api_v1_prefix + "/")
            or request.method == "OPTIONS"
            or (path == settings.api_v1_prefix + "/health" and request.method in {"GET", "HEAD"})
        ):
            return await self.app(scope, receive, send)
        try:
            # Do not let a saturated limiter hold request workers indefinitely.
            async with asyncio.timeout(3):
                retry = await self.limiter.check(request_budgets(request, settings))
        except (SQLAlchemyError, TimeoutError):
            logger.error("API rate-limit storage unavailable", exc_info=True)
            response = JSONResponse(
                {
                    "statusCode": 503,
                    "errorCode": "rate_limit_unavailable",
                    "message": "Servicio temporalmente ocupado. Intenta nuevamente.",
                    "retryAfter": 5,
                },
                status_code=503,
                headers={"Retry-After": "5", "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        if retry is not None:
            response = JSONResponse(
                {
                    "statusCode": 429,
                    "errorCode": "rate_limited",
                    "message": "Demasiadas solicitudes. Intenta nuevamente más tarde.",
                    "retryAfter": retry,
                },
                status_code=429,
                headers={"Retry-After": str(retry), "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)
