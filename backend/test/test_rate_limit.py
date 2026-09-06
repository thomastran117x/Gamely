from __future__ import annotations

import pytest

from src.infrastructure.redis import RedisRateLimiter
from test.test_auth_unit import FakeRedis


def make_limiter() -> tuple[RedisRateLimiter, FakeRedis]:
    redis = FakeRedis()
    return RedisRateLimiter(redis), redis  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_limiter_allows_requests_up_to_the_limit() -> None:
    limiter, _ = make_limiter()

    results = [await limiter.hit("throttle:a", 3, 60) for _ in range(3)]

    assert results == [True, True, True]


@pytest.mark.asyncio
async def test_limiter_blocks_the_request_after_the_limit() -> None:
    limiter, _ = make_limiter()
    for _ in range(3):
        await limiter.hit("throttle:a", 3, 60)

    assert await limiter.hit("throttle:a", 3, 60) is False


@pytest.mark.asyncio
async def test_limiter_counts_each_key_separately() -> None:
    limiter, redis = make_limiter()

    await limiter.hit("throttle:a", 1, 60)

    assert await limiter.hit("throttle:b", 1, 60) is True
    assert redis.values == {"throttle:a": "1", "throttle:b": "1"}


@pytest.mark.asyncio
async def test_limiter_fails_closed_on_an_unreadable_reply() -> None:
    limiter, redis = make_limiter()

    async def broken_eval(*_: object, **__: object) -> object:
        return None

    redis.eval = broken_eval  # type: ignore[method-assign]

    assert await limiter.hit("throttle:a", 100, 60) is False
