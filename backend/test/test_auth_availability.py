from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from fastapi import Request

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_model import User
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.availability.availability_service import (
    EmailAvailabilityService,
)
from src.features.auth.availability.email_filter import EmailFilter
from src.infrastructure.redis import RedisRateLimiter
from src.shared.exceptions import (
    BadRequestError,
    ServiceUnavailableError,
    TooManyRequestsError,
)
from src.shared.requests.client_address import client_address
from test.test_auth_service import FakeRepository
from test.test_auth_unit import FakeRedis, mark_filter_ready

JWT_SECRET = "test-secret-with-at-least-thirty-two-characters"


def make_service(
    repository: FakeRepository | None = None,
    redis: FakeRedis | None = None,
    **overrides: object,
) -> tuple[EmailAvailabilityService, FakeRepository, FakeRedis]:
    repository = repository or FakeRepository()
    redis = redis or FakeRedis()
    settings = Settings(auth_jwt_secret=JWT_SECRET, **overrides)  # type: ignore[arg-type]
    mark_filter_ready(redis, settings)
    service = EmailAvailabilityService(
        cast(AuthRepository, repository),
        EmailFilter(redis, settings),  # type: ignore[arg-type]
        RedisRateLimiter(redis),  # type: ignore[arg-type]
        settings,
    )
    return service, repository, redis


def make_user(email: str = "user@example.com") -> User:
    return User(id=uuid4(), email=email, password_hash=None, email_verified_at=None)


def make_request(host: str) -> Request:
    return cast(Request, SimpleNamespace(client=SimpleNamespace(host=host)))


@pytest.mark.asyncio
async def test_availability_answers_without_a_lookup_on_a_filter_miss() -> None:
    service, repository, _ = make_service()

    assert await service.is_available("fresh@example.com", "1.2.3.4") is True
    assert repository.by_email_calls == 0


@pytest.mark.asyncio
async def test_availability_consults_the_database_on_a_filter_hit() -> None:
    existing = make_user()
    redis = FakeRedis()
    redis.false_positives.add(existing.email)
    service, repository, _ = make_service(FakeRepository([existing]), redis)

    assert await service.is_available(existing.email, "1.2.3.4") is False
    assert repository.by_email_calls == 1


@pytest.mark.asyncio
async def test_availability_clears_a_false_positive_against_the_database() -> None:
    redis = FakeRedis()
    redis.false_positives.add("fresh@example.com")
    service, repository, _ = make_service(redis=redis)

    assert await service.is_available("fresh@example.com", "1.2.3.4") is True
    assert repository.by_email_calls == 1


@pytest.mark.asyncio
async def test_availability_normalizes_before_asking() -> None:
    existing = make_user()
    redis = FakeRedis()
    redis.false_positives.add(existing.email)
    service, _, _ = make_service(FakeRepository([existing]), redis)

    assert await service.is_available("  User@Example.COM ", "1.2.3.4") is False


@pytest.mark.asyncio
async def test_availability_rejects_a_malformed_address() -> None:
    service, _, _ = make_service()

    with pytest.raises(BadRequestError):
        await service.is_available("missing-at-sign", "1.2.3.4")


@pytest.mark.asyncio
async def test_availability_raises_too_many_requests_past_the_limit() -> None:
    service, _, _ = make_service(auth_availability_limit=2)

    for _ in range(2):
        await service.is_available("fresh@example.com", "1.2.3.4")

    with pytest.raises(TooManyRequestsError) as error:
        await service.is_available("fresh@example.com", "1.2.3.4")
    assert error.value.status_code == 429
    assert error.value.code == "too_many_requests"


@pytest.mark.asyncio
async def test_availability_throttles_each_caller_separately() -> None:
    service, _, _ = make_service(auth_availability_limit=1)
    await service.is_available("fresh@example.com", "1.2.3.4")

    assert await service.is_available("fresh@example.com", "5.6.7.8") is True


@pytest.mark.asyncio
async def test_availability_scopes_are_counted_independently() -> None:
    service, _, _ = make_service(auth_availability_limit=1)
    await service.is_available("fresh@example.com", "1.2.3.4")

    # A spent availability budget must not also block signup.
    await service.throttle("1.2.3.4", "signup", 1)


@pytest.mark.asyncio
async def test_availability_fails_closed_when_redis_is_unavailable() -> None:
    service, _, redis = make_service()

    async def broken_eval(*_: object, **__: object) -> object:
        raise ConnectionError("redis is unreachable")

    redis.eval = broken_eval  # type: ignore[method-assign]

    with pytest.raises(ServiceUnavailableError):
        await service.is_available("fresh@example.com", "1.2.3.4")


@pytest.mark.asyncio
async def test_availability_does_not_store_the_raw_caller_address() -> None:
    service, _, redis = make_service()

    await service.is_available("fresh@example.com", "203.0.113.7")

    assert redis.values
    assert all("203.0.113.7" not in key for key in redis.values)


def test_client_address_collapses_ipv6_onto_its_network() -> None:
    assert client_address(make_request("2001:db8:1:2:aaaa:bbbb:cccc:dddd")) == (
        "2001:db8:1:2::"
    )


def test_client_address_keeps_ipv4_intact() -> None:
    assert client_address(make_request("203.0.113.7")) == "203.0.113.7"


def test_client_address_passes_through_a_non_address_host() -> None:
    # TestClient reports "testclient"; parsing it must not raise.
    assert client_address(make_request("testclient")) == "testclient"


def test_client_address_handles_a_missing_client() -> None:
    assert client_address(cast(Request, SimpleNamespace(client=None))) == "unknown"
