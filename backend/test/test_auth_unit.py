from __future__ import annotations

from builtins import set as builtin_set
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import jwt

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_service import AuthService
from src.features.auth.token.token_service import TokenService
from redis.exceptions import ResponseError

from src.shared.exceptions import UnauthorizedError


JWT_SECRET = "test-secret-with-at-least-thirty-two-characters"


def token_service(redis: FakeRedis | None = None) -> tuple[TokenService, FakeRedis]:
    redis = redis or FakeRedis()
    return (
        TokenService(
            redis,  # type: ignore[arg-type]
            Settings(auth_jwt_secret=JWT_SECRET),
        ),
        redis,
    )


class FakePipelineCuckoo:
    def __init__(self, pipeline: FakePipeline) -> None:
        self._pipeline = pipeline

    def exists(self, *args: Any, **kwargs: Any) -> None:
        self._pipeline.record("cf.exists", args, kwargs)

    def insert(self, *args: Any, **kwargs: Any) -> None:
        self._pipeline.record("cf.insert", args, kwargs)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def record(
        self, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        self._operations.append((method, args, kwargs))

    def cf(self) -> FakePipelineCuckoo:
        return FakePipelineCuckoo(self)

    def set(self, *args: Any, **kwargs: Any) -> None:
        self.record("set", args, kwargs)

    def get(self, *args: Any, **kwargs: Any) -> None:
        self.record("get", args, kwargs)

    def sadd(self, *args: Any, **kwargs: Any) -> None:
        self.record("sadd", args, kwargs)

    def srem(self, *args: Any, **kwargs: Any) -> None:
        self.record("srem", args, kwargs)

    def expire(self, *args: Any, **kwargs: Any) -> None:
        self.record("expire", args, kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> None:
        self.record("delete", args, kwargs)

    async def execute(self) -> list[Any]:
        results = []
        for method, args, kwargs in self._operations:
            target: Any = self._redis
            if method.startswith("cf."):
                target, method = self._redis.cf(), method[3:]
            results.append(await getattr(target, method)(*args, **kwargs))
        return results


class FakeCuckoo:
    """In-memory stand-in for the CF.* command set."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis

    async def create(
        self,
        key: str,
        capacity: int,
        expansion: int | None = None,
        bucket_size: int | None = None,
        max_iterations: int | None = None,
    ) -> bool:
        self._redis.raise_filter_error()
        if key in self._redis.filters:
            raise ResponseError("ERR item exists")
        self._redis.filters[key] = builtin_set()
        self._redis.reserved[key] = (capacity, bucket_size, expansion, max_iterations)
        return True

    async def insert(
        self,
        key: str,
        items: list[str],
        capacity: int | None = None,
        nocreate: bool | None = None,
    ) -> list[int]:
        self._redis.raise_filter_error()
        stored = self._redis.filters.setdefault(key, builtin_set())
        self._redis.reserved.setdefault(key, (capacity, None, None, None))
        added = []
        for item in items:
            added.append(0 if item in stored else 1)
            stored.add(item)
            self._redis.inserted[key] = self._redis.inserted.get(key, 0) + 1
        return added

    async def exists(self, key: str, item: str) -> int:
        self._redis.raise_filter_error()
        stored = self._redis.filters.get(key, builtin_set())
        return 1 if item in stored or item in self._redis.false_positives else 0

    async def delete(self, key: str, item: str) -> int:
        self._redis.raise_filter_error()
        stored = self._redis.filters.get(key, builtin_set())
        if item not in stored:
            return 0
        stored.discard(item)
        self._redis.deleted[key] = self._redis.deleted.get(key, 0) + 1
        return 1

    async def info(self, key: str) -> dict[str, int]:
        self._redis.raise_filter_error()
        if key not in self._redis.filters:
            raise ResponseError("ERR not found")
        return {
            "Number of items inserted": self._redis.inserted.get(key, 0),
            "Number of items deleted": self._redis.deleted.get(key, 0),
        }


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, builtin_set[str]] = {}
        self.filters: dict[str, builtin_set[str]] = {}
        self.reserved: dict[str, tuple[int | None, ...]] = {}
        self.inserted: dict[str, int] = {}
        self.deleted: dict[str, int] = {}
        # Reported by CF.EXISTS for addresses that were never added, so the
        # false-positive fall-through can be tested without probabilistic hashing.
        self.false_positives: builtin_set[str] = builtin_set()
        # Applies to the CF.* path only, leaving plain key commands working.
        self.filter_error: Exception | None = None

    def raise_filter_error(self) -> None:
        if self.filter_error is not None:
            raise self.filter_error

    def cf(self) -> FakeCuckoo:
        return FakeCuckoo(self)

    async def eval(self, script: str, _: int, *args: Any) -> Any:
        if "rotated" not in script and "INCR" not in script:
            # Compare-and-delete lock release.
            key, token = str(args[0]), str(args[1])
            if self.values.get(key) != token:
                return 0
            return await self.delete(key)
        if "INCR" in script:
            key, window = str(args[0]), args[1]
            hits = await self.incr(key)
            if hits == 1:
                await self.expire(key, window)
            return hits
        refresh_key, reuse_key = str(args[0]), str(args[1])
        session = self.values.get(refresh_key)
        if session is not None:
            await self.delete(refresh_key)
            self.values[reuse_key] = session
            return ["rotated", session]
        reused_session = self.values.pop(reuse_key, None)
        return ["reused", reused_session] if reused_session else ["missing", ""]

    async def incr(self, key: str) -> int:
        hits = int(self.values.get(key, "0")) + 1
        self.values[key] = str(hits)
        return hits

    async def exists(self, *keys: str) -> int:
        return sum(
            1
            for key in keys
            if key in self.values or key in self.sets or key in self.filters
        )

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def set(self, key: str, value: str, **kwargs: Any) -> bool:
        if kwargs.get("nx") and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if self.values.pop(key, None) is not None:
                deleted += 1
            if self.sets.pop(key, None) is not None:
                deleted += 1
            if self.filters.pop(key, None) is not None:
                self.reserved.pop(key, None)
                self.inserted.pop(key, None)
                self.deleted.pop(key, None)
                deleted += 1
        return deleted

    async def sadd(self, key: str, value: str) -> None:
        self.sets.setdefault(key, builtin_set()).add(value)

    async def srem(self, key: str, value: str) -> None:
        self.sets.setdefault(key, builtin_set()).discard(value)

    async def smembers(self, key: str) -> builtin_set[str]:
        return builtin_set(self.sets.get(key, builtin_set()))

    async def expire(self, _: str, __: timedelta | int) -> None:
        return None

    async def ttl(self, _: str) -> int:
        return 60


def mark_filter_ready(redis: FakeRedis, settings: Settings) -> None:
    """Put the fake in the state the startup warmer leaves behind."""
    from src.features.auth.availability.email_filter import build_email_filter

    email_filter = build_email_filter(redis, settings)  # type: ignore[arg-type]
    redis.values[email_filter.meta_key] = email_filter.fingerprint
    redis.filters.setdefault(email_filter.key, builtin_set())


@pytest.mark.asyncio
async def test_refresh_token_rotates_and_cannot_be_reused() -> None:
    tokens, redis = token_service()
    user_id = uuid4()

    refresh = await tokens.issue_refresh(user_id)

    rotated_user_id, family_id = await tokens.rotate_refresh(refresh)
    assert rotated_user_id == user_id
    replacement = await tokens.issue_refresh(user_id, family_id)
    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(refresh)
    assert await redis.smembers(f"auth:sessions:{user_id}") == set()
    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(replacement)


@pytest.mark.asyncio
async def test_reused_refresh_does_not_revoke_a_new_session_family() -> None:
    tokens, redis = token_service()
    user_id = uuid4()
    stale = await tokens.issue_refresh(user_id)
    _, family_id = await tokens.rotate_refresh(stale)
    await tokens.issue_refresh(user_id, family_id)
    unrelated = await tokens.issue_refresh(user_id)

    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(stale)
    assert (await tokens.rotate_refresh(unrelated))[0] == user_id
    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(stale)


def test_access_token_contains_expected_claims_and_decodes_subject() -> None:
    tokens, _ = token_service()
    user_id = uuid4()

    encoded = tokens.issue_access(user_id, "user@example.com", True)
    claims = jwt.decode(
        encoded,
        JWT_SECRET,
        algorithms=["HS256"],
        audience="games-api",
        issuer="games-api",
    )

    assert tokens.decode_access(encoded) == user_id
    assert claims["email"] == "user@example.com"
    assert claims["verified"] is True
    assert claims["exp"] - claims["iat"] == 15 * 60


@pytest.mark.parametrize(
    "claims",
    [
        {
            "sub": str(uuid4()),
            "iss": "wrong-issuer",
            "aud": "games-api",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        {
            "sub": str(uuid4()),
            "iss": "games-api",
            "aud": "wrong-audience",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        {
            "sub": str(uuid4()),
            "iss": "games-api",
            "aud": "games-api",
            "exp": datetime.now(UTC) - timedelta(seconds=1),
        },
        {
            "sub": "not-a-uuid",
            "iss": "games-api",
            "aud": "games-api",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
    ],
)
def test_access_token_rejects_invalid_security_claims(
    claims: dict[str, object],
) -> None:
    tokens, _ = token_service()
    encoded = jwt.encode(claims, JWT_SECRET, algorithm="HS256")

    with pytest.raises(UnauthorizedError, match="invalid or expired"):
        tokens.decode_access(encoded)


@pytest.mark.asyncio
async def test_invalid_refresh_token_is_rejected() -> None:
    tokens, _ = token_service()

    with pytest.raises(UnauthorizedError, match="invalid or expired"):
        await tokens.rotate_refresh("unknown-refresh-token")


@pytest.mark.asyncio
async def test_current_device_logout_does_not_revoke_other_family() -> None:
    tokens, _ = token_service()
    user_id = uuid4()
    current = await tokens.issue_refresh(user_id)
    other = await tokens.issue_refresh(user_id)

    await tokens.revoke_refresh(current)

    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(current)
    assert (await tokens.rotate_refresh(other))[0] == user_id


@pytest.mark.asyncio
async def test_logout_all_revokes_every_refresh_family() -> None:
    tokens, _ = token_service()
    user_id = uuid4()
    first = await tokens.issue_refresh(user_id)
    second = await tokens.issue_refresh(user_id)

    await tokens.revoke_all(user_id)

    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(first)
    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(second)


@pytest.mark.asyncio
async def test_verification_code_is_single_use_and_counts_failed_attempts() -> None:
    redis = FakeRedis()
    service = object.__new__(AuthService)
    service._redis = redis  # type: ignore[assignment]

    await redis.set("auth:code:verify:user@example.com", "wrong:0")
    assert await service._consume_code("user@example.com", "verify", "123456") is False
    assert await redis.get("auth:code:verify:user@example.com") == "wrong:1"

    digest = __import__("hashlib").sha256(b"123456").hexdigest()
    await redis.set("auth:code:verify:user@example.com", f"{digest}:0")
    assert await service._consume_code("user@example.com", "verify", "123456") is True
    assert await redis.get("auth:code:verify:user@example.com") is None
