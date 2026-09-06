from __future__ import annotations

import pytest
from redis.exceptions import ResponseError

from src.application.environment.environment_manager import Settings
from src.features.auth.availability.email_filter import (
    EMAIL_FILTER_KEY,
    EmailFilter,
)
from test.test_auth_unit import FakeRedis, mark_filter_ready

JWT_SECRET = "test-secret-with-at-least-thirty-two-characters"


def make_filter(
    redis: FakeRedis | None = None, **overrides: object
) -> tuple[EmailFilter, FakeRedis, Settings]:
    redis = redis or FakeRedis()
    settings = Settings(auth_jwt_secret=JWT_SECRET, **overrides)  # type: ignore[arg-type]
    mark_filter_ready(redis, settings)
    return EmailFilter(redis, settings), redis, settings  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_filter_reports_a_hit_after_remembering_an_address() -> None:
    emails, redis, _ = make_filter()

    await emails.remember("user@example.com")

    assert await emails.might_exist("user@example.com") is True
    assert redis.filters[EMAIL_FILTER_KEY] == {"user@example.com"}


@pytest.mark.asyncio
async def test_filter_reports_a_miss_for_an_unknown_address() -> None:
    emails, _, _ = make_filter()

    assert await emails.might_exist("stranger@example.com") is False


@pytest.mark.asyncio
async def test_filter_reports_a_hit_when_redis_fails() -> None:
    emails, redis, _ = make_filter()
    redis.filter_error = ResponseError("unknown command 'CF.EXISTS'")

    # Degrading towards "ask the database" keeps every caller correct.
    assert await emails.might_exist("user@example.com") is True


@pytest.mark.asyncio
async def test_filter_swallows_write_failures() -> None:
    emails, redis, _ = make_filter()
    redis.filter_error = ConnectionError("redis is unreachable")

    await emails.remember("user@example.com")

    assert redis.filters[EMAIL_FILTER_KEY] == set()


@pytest.mark.asyncio
async def test_filter_uses_the_namespaced_key() -> None:
    emails, redis, _ = make_filter()

    await emails.remember("user@example.com")

    assert list(redis.filters) == ["auth:email:filter"]


@pytest.mark.asyncio
async def test_filter_writes_carry_the_configured_capacity() -> None:
    emails, redis, _ = make_filter(auth_email_filter_capacity=4242)

    await emails.remember("user@example.com")

    capacity, *_ = redis.reserved[EMAIL_FILTER_KEY]
    assert capacity == 4242


@pytest.mark.asyncio
async def test_filter_stops_being_trusted_when_a_write_is_lost() -> None:
    emails, redis, _ = make_filter()
    await emails.remember("known@example.com")
    redis.filter_error = ConnectionError("redis is unreachable")

    await emails.remember("lost@example.com")

    # The caller commits the address regardless, so a filter still trusted after
    # dropping it would report a miss for a registered address.
    redis.filter_error = None
    assert await emails.might_exist("lost@example.com") is True
    assert await emails.might_exist("anyone@example.com") is True


@pytest.mark.asyncio
async def test_filter_survives_a_failure_to_withdraw_trust() -> None:
    emails, redis, _ = make_filter()

    async def unreachable(*_: object, **__: object) -> object:
        raise ConnectionError("redis is unreachable")

    redis.filter_error = ConnectionError("redis is unreachable")
    redis.delete = unreachable  # type: ignore[method-assign, assignment]

    await emails.remember("lost@example.com")

    assert await emails.might_exist("lost@example.com") is True


@pytest.mark.asyncio
async def test_filter_reports_a_hit_until_the_warmer_marks_it_built() -> None:
    redis = FakeRedis()
    settings = Settings(auth_jwt_secret=JWT_SECRET)
    emails = EmailFilter(redis, settings)  # type: ignore[arg-type]

    await emails.remember("user@example.com")

    # An unmarked filter is absent or half-built, so a miss would be a lie.
    assert await emails.might_exist("stranger@example.com") is True


@pytest.mark.asyncio
async def test_filter_does_not_touch_redis_when_disabled() -> None:
    emails, redis, _ = make_filter(auth_email_filter_enabled=False)

    await emails.remember("user@example.com")

    assert redis.filters[EMAIL_FILTER_KEY] == set()
    assert await emails.might_exist("user@example.com") is True
