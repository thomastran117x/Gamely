from __future__ import annotations

from datetime import timedelta
from builtins import set as builtin_set
from typing import Any, Set
from uuid import uuid4

import pytest

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_service import AuthService
from src.features.auth.token.token_service import TokenService
from src.shared.exceptions import UnauthorizedError


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
        self.sets: dict[str, Set[str]] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    async def set(self, key: str, value: str, **_: Any) -> bool:
        self.values[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
            self.sets.pop(key, None)
        return len(keys)

    async def sadd(self, key: str, value: str) -> None:
        self.sets.setdefault(key, builtin_set()).add(value)

    async def srem(self, key: str, value: str) -> None:
        self.sets.setdefault(key, builtin_set()).discard(value)

    async def smembers(self, key: str) -> Set[str]:
        return self.sets.get(key, builtin_set())

    async def expire(self, _: str, __: timedelta) -> None:
        return None

    async def ttl(self, _: str) -> int:
        return 60


@pytest.mark.asyncio
async def test_refresh_token_rotates_and_cannot_be_reused() -> None:
    redis = FakeRedis()
    tokens = TokenService(redis, Settings())  # type: ignore[arg-type]
    user_id = uuid4()

    refresh = await tokens.issue_refresh(user_id)

    assert await tokens.rotate_refresh(refresh) == user_id
    with pytest.raises(UnauthorizedError):
        await tokens.rotate_refresh(refresh)


@pytest.mark.asyncio
async def test_verification_code_is_single_use_and_counts_failed_attempts() -> None:
    redis = FakeRedis()
    service = object.__new__(AuthService)
    service._redis = redis  # type: ignore[attr-defined, assignment]

    await redis.set("auth:code:verify:user@example.com", "wrong:0")
    assert await service._consume_code("user@example.com", "verify", "123456") is False
    assert await redis.get("auth:code:verify:user@example.com") == "wrong:1"

    digest = __import__("hashlib").sha256(b"123456").hexdigest()
    await redis.set("auth:code:verify:user@example.com", f"{digest}:0")
    assert await service._consume_code("user@example.com", "verify", "123456") is True
    assert await redis.get("auth:code:verify:user@example.com") is None



