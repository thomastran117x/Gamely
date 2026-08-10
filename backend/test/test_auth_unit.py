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


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._operations: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def set(self, *args: Any, **kwargs: Any) -> None:
        self._operations.append(("set", args, kwargs))

    def sadd(self, *args: Any, **kwargs: Any) -> None:
        self._operations.append(("sadd", args, kwargs))

    def srem(self, *args: Any, **kwargs: Any) -> None:
        self._operations.append(("srem", args, kwargs))

    def expire(self, *args: Any, **kwargs: Any) -> None:
        self._operations.append(("expire", args, kwargs))

    def delete(self, *args: Any, **kwargs: Any) -> None:
        self._operations.append(("delete", args, kwargs))

    async def execute(self) -> None:
        for method, args, kwargs in self._operations:
            await getattr(self._redis, method)(*args, **kwargs)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, builtin_set[str]] = {}

    async def eval(
        self, _: str, __: int, refresh_key: str, reuse_key: str, ___: int
    ) -> list[str]:
        session = self.values.get(refresh_key)
        if session is not None:
            await self.delete(refresh_key)
            self.values[reuse_key] = session
            return ["rotated", session]
        reused_session = self.values.pop(reuse_key, None)
        return ["reused", reused_session] if reused_session else ["missing", ""]

    def pipeline(self) -> FakePipeline:
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
        return deleted

    async def sadd(self, key: str, value: str) -> None:
        self.sets.setdefault(key, builtin_set()).add(value)

    async def srem(self, key: str, value: str) -> None:
        self.sets.setdefault(key, builtin_set()).discard(value)

    async def smembers(self, key: str) -> builtin_set[str]:
        return builtin_set(self.sets.get(key, builtin_set()))

    async def expire(self, _: str, __: timedelta) -> None:
        return None

    async def ttl(self, _: str) -> int:
        return 60


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
