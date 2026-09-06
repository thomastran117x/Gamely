from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from types import TracebackType
from typing import cast
from uuid import uuid4

import pytest
from redis.exceptions import ResponseError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_model import User
from src.features.auth.availability.email_filter import (
    EMAIL_FILTER_KEY,
    EMAIL_FILTER_META_KEY,
)
from src.features.auth.availability.email_filter_warmer import (
    EMAIL_FILTER_LOCK_KEY,
    EmailFilterWarmer,
)
from test.test_auth_unit import FakeRedis

JWT_SECRET = "test-secret-with-at-least-thirty-two-characters"


class FakeSession:
    """Stand-in for AsyncSession that only serves the warmer's two queries."""

    def __init__(self, emails: list[str], error: Exception | None = None) -> None:
        self.emails = emails
        self.error = error
        self.batch_sizes: list[int] = []

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(
        self,
        _: type[BaseException] | None,
        __: BaseException | None,
        ___: TracebackType | None,
    ) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, emails: list[str], error: Exception | None = None) -> None:
        self.session = FakeSession(emails, error)

    def __call__(self) -> FakeSession:
        return self.session


class FakeWarmerRepository:
    """Replaces AuthRepository inside the warmer via monkeypatching."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def count_users(self) -> int:
        if self._session.error is not None:
            raise self._session.error
        return len(self._session.emails)

    async def iter_email_batches(self, batch_size: int) -> AsyncIterator[Sequence[str]]:
        if self._session.error is not None:
            raise self._session.error
        for start in range(0, len(self._session.emails), batch_size):
            batch = self._session.emails[start : start + batch_size]
            self._session.batch_sizes.append(len(batch))
            yield batch


@pytest.fixture(autouse=True)
def use_fake_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.features.auth.availability.email_filter_warmer.AuthRepository",
        FakeWarmerRepository,
    )


def make_warmer(
    emails: list[str] | None = None,
    redis: FakeRedis | None = None,
    error: Exception | None = None,
    **overrides: object,
) -> tuple[EmailFilterWarmer, FakeRedis, FakeSessionFactory]:
    redis = redis or FakeRedis()
    factory = FakeSessionFactory(emails or [], error)
    settings = Settings(auth_jwt_secret=JWT_SECRET, **overrides)  # type: ignore[arg-type]
    warmer = EmailFilterWarmer(
        redis,  # type: ignore[arg-type]
        cast(async_sessionmaker[AsyncSession], factory),
        settings,
    )
    return warmer, redis, factory


def make_user(email: str) -> User:
    return User(id=uuid4(), email=email, password_hash=None, email_verified_at=None)


@pytest.mark.asyncio
async def test_warmer_builds_the_filter_when_the_key_is_absent() -> None:
    warmer, redis, _ = make_warmer(["a@example.com", "b@example.com"])

    await warmer.warm()

    assert redis.filters[EMAIL_FILTER_KEY] == {"a@example.com", "b@example.com"}
    assert redis.values[EMAIL_FILTER_META_KEY]


@pytest.mark.asyncio
async def test_warmer_skips_the_rebuild_when_the_filter_is_current() -> None:
    warmer, redis, factory = make_warmer(["a@example.com"])
    await warmer.warm()
    factory.session.batch_sizes.clear()

    await warmer.warm()

    assert factory.session.batch_sizes == []


@pytest.mark.asyncio
async def test_warmer_rebuilds_when_the_parameter_fingerprint_changes() -> None:
    warmer, redis, _ = make_warmer(["a@example.com"])
    await warmer.warm()

    resized, _, factory = make_warmer(
        ["a@example.com"], redis=redis, auth_email_filter_capacity=5000
    )
    await resized.warm()

    assert factory.session.batch_sizes == [1]
    capacity, *_ = redis.reserved[EMAIL_FILTER_KEY]
    assert capacity == 5000


@pytest.mark.asyncio
async def test_warmer_rebuilds_when_the_filter_lost_recent_writes() -> None:
    warmer, redis, _ = make_warmer(["a@example.com", "b@example.com"])
    await warmer.warm()
    # Simulate a snapshot restore that kept the key but dropped an entry.
    redis.filters[EMAIL_FILTER_KEY].discard("b@example.com")
    redis.inserted[EMAIL_FILTER_KEY] = 1

    await warmer.warm()

    assert redis.filters[EMAIL_FILTER_KEY] == {"a@example.com", "b@example.com"}


@pytest.mark.asyncio
async def test_warmer_skips_the_rebuild_when_another_replica_holds_the_lock() -> None:
    warmer, redis, factory = make_warmer(["a@example.com"])
    redis.values[EMAIL_FILTER_LOCK_KEY] = "another-replica"

    await warmer.warm()

    assert factory.session.batch_sizes == []
    assert redis.filters == {}


@pytest.mark.asyncio
async def test_warmer_releases_the_lock_after_a_rebuild() -> None:
    warmer, redis, _ = make_warmer(["a@example.com"])

    await warmer.warm()

    assert EMAIL_FILTER_LOCK_KEY not in redis.values


@pytest.mark.asyncio
async def test_warmer_releases_the_lock_after_a_failed_rebuild() -> None:
    warmer, redis, _ = make_warmer(["a@example.com"])
    redis.filter_error = ResponseError("unknown command 'CF.RESERVE'")

    await warmer.warm()

    assert EMAIL_FILTER_LOCK_KEY not in redis.values


@pytest.mark.asyncio
async def test_warmer_leaves_a_lock_owned_by_another_replica_alone() -> None:
    warmer, redis, _ = make_warmer(["a@example.com"])
    original_set = redis.set

    async def steal_lock_after_acquiring(
        key: str, value: str, **kwargs: object
    ) -> bool:
        acquired = await original_set(key, value, **kwargs)
        if key == EMAIL_FILTER_LOCK_KEY and acquired:
            redis.values[key] = "successor-replica"
        return acquired

    redis.set = steal_lock_after_acquiring  # type: ignore[method-assign]

    await warmer.warm()

    assert redis.values[EMAIL_FILTER_LOCK_KEY] == "successor-replica"


@pytest.mark.asyncio
async def test_warmer_streams_addresses_in_configured_batches() -> None:
    warmer, _, factory = make_warmer(
        [f"user{index}@example.com" for index in range(5)],
        auth_email_filter_rebuild_batch_size=2,
    )

    await warmer.warm()

    assert factory.session.batch_sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_warmer_normalizes_stored_addresses() -> None:
    warmer, redis, _ = make_warmer(["  User@Example.COM "])

    await warmer.warm()

    assert redis.filters[EMAIL_FILTER_KEY] == {"user@example.com"}


@pytest.mark.asyncio
async def test_warmer_swallows_database_errors() -> None:
    warmer, redis, _ = make_warmer(["a@example.com"], error=RuntimeError("no database"))

    await warmer.warm()

    # A half-built filter must stay unmarked, so lookups keep asking the database.
    assert redis.filters[EMAIL_FILTER_KEY] == set()
    assert EMAIL_FILTER_META_KEY not in redis.values


@pytest.mark.asyncio
async def test_warmer_swallows_a_missing_module() -> None:
    warmer, redis, _ = make_warmer(["a@example.com"])
    redis.filter_error = ResponseError("unknown command 'CF.RESERVE'")

    await warmer.warm()

    assert redis.filters == {}


@pytest.mark.asyncio
async def test_warmer_does_nothing_when_the_filter_is_disabled() -> None:
    warmer, redis, factory = make_warmer(
        ["a@example.com"], auth_email_filter_enabled=False
    )

    await warmer.warm()

    assert factory.session.batch_sizes == []
    assert redis.filters == {}
