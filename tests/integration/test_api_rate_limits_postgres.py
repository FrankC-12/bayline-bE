"""Real PostgreSQL budgets serialize concurrent traffic across server instances."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.core.rate_limiting import ApiRateBucket, Budget, PostgresRateLimiter


@pytest.mark.asyncio
async def test_concurrent_instances_share_atomic_budget(billing_db):
    ctx = billing_db
    now = 1800000001.0
    instances = [PostgresRateLimiter(ctx.sessions, clock=lambda: now) for _ in range(3)]
    budget = Budget(f"test-concurrent:{uuid.uuid4()}", 7)
    results = await asyncio.gather(*(instances[i % 3].check([budget]) for i in range(30)))
    assert results.count(None) == 7
    assert all(result == 59 for result in results if result is not None)


@pytest.mark.asyncio
async def test_limits_reset_and_cleanup_removes_expired_buckets(billing_db):
    ctx = billing_db
    clock = [1800000001.0]
    limiter = PostgresRateLimiter(ctx.sessions, clock=lambda: clock[0])
    budget = Budget(f"test-window:{uuid.uuid4()}", 1)
    assert await limiter.check([budget]) is None
    assert await limiter.check([budget]) == 59
    clock[0] += 60
    assert await limiter.check([budget]) is None
    async with ctx.sessions() as db:
        expired = await db.scalar(
            select(func.count())
            .select_from(ApiRateBucket)
            .where(ApiRateBucket.expires_at < datetime.fromtimestamp(clock[0], UTC))
        )
        assert expired == 0


@pytest.mark.asyncio
async def test_multi_budget_rejections_remain_counted_across_new_instances(billing_db):
    ctx = billing_db
    first = PostgresRateLimiter(ctx.sessions, clock=lambda: 1800000001.0)
    second = PostgresRateLimiter(ctx.sessions, clock=lambda: 1800000001.0)
    prefix = uuid.uuid4().hex
    shared = Budget(f"test-ip:{prefix}", 2)
    user_a = Budget(f"test-user-a:{prefix}", 1)
    user_b = Budget(f"test-user-b:{prefix}", 10)
    assert await first.check([shared, user_a]) is None
    assert await first.check([shared, user_a]) == 59
    # The rejected request used the outer IP budget too: switching users cannot evade it.
    assert await second.check([shared, user_b]) == 59


@pytest.mark.asyncio
async def test_confirmed_blocks_skip_database_until_window_expires(billing_db):
    ctx = billing_db
    clock = [1800000001.0]
    transactions = []

    def sessions():
        transactions.append(True)
        return ctx.sessions()

    limiter = PostgresRateLimiter(sessions, clock=lambda: clock[0])
    budget = Budget(f"test-cached-block:{uuid.uuid4()}", 1)
    assert await limiter.check([budget]) is None
    assert await limiter.check([budget]) == 59
    for _ in range(100):
        assert await limiter.check([budget]) == 59
    assert len(transactions) == 2

    clock[0] += 10
    assert await limiter.check([budget]) == 49
    assert len(transactions) == 2
    clock[0] += 50
    assert await limiter.check([budget]) is None
    assert len(transactions) == 3


@pytest.mark.asyncio
async def test_block_cache_eviction_rechecks_shared_budget_without_allowing_traffic(billing_db):
    ctx = billing_db
    transactions = []

    def sessions():
        transactions.append(True)
        return ctx.sessions()

    limiter = PostgresRateLimiter(sessions, clock=lambda: 1800000001.0, blocked_cache_size=2)
    budgets = [Budget(f"test-eviction:{uuid.uuid4()}", 1) for _ in range(3)]
    for budget in budgets:
        assert await limiter.check([budget]) is None
        assert await limiter.check([budget]) == 59
    assert len(transactions) == 6
    assert len(limiter.blocked) == 2
    # The oldest entry was evicted; PostgreSQL must still reject it.
    assert await limiter.check([budgets[0]]) == 59
    assert len(transactions) == 7
    # A cached rejection for one identity does not block another identity.
    assert await limiter.check([Budget(f"test-independent:{uuid.uuid4()}", 1)]) is None


@pytest.mark.asyncio
async def test_block_cache_never_reuses_rejections_for_a_different_limit(billing_db):
    limiter = PostgresRateLimiter(billing_db.sessions, clock=lambda: 1800000001.0)
    key = f"test-budget-change:{uuid.uuid4()}"
    assert await limiter.check([Budget(key, 1)]) is None
    assert await limiter.check([Budget(key, 1)]) == 59
    assert await limiter.check([Budget(key, 3)]) is None
