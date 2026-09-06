from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import ResponseError


class RedisCuckooFilter:
    """Cuckoo filter (CF.*) bound to a single Redis key.

    Membership answers are one-sided: a miss is definitive, a hit may be a false
    positive. Redis failures propagate; callers decide how to degrade.
    """

    def __init__(
        self,
        redis: Redis,
        key: str,
        meta_key: str,
        *,
        capacity: int,
        bucket_size: int,
        expansion: int,
        max_iterations: int,
    ) -> None:
        self._redis = redis
        self._key = key
        self._meta_key = meta_key
        self._capacity = capacity
        self._bucket_size = bucket_size
        self._expansion = expansion
        self._max_iterations = max_iterations

    @property
    def key(self) -> str:
        return self._key

    @property
    def meta_key(self) -> str:
        return self._meta_key

    @property
    def fingerprint(self) -> str:
        """Identify the parameters the key was created with.

        CF.RESERVE freezes its parameters, so a settings change only takes effect
        once a caller notices this value has moved and rebuilds the filter.
        """
        return (
            f"{self._capacity}:{self._bucket_size}:"
            f"{self._expansion}:{self._max_iterations}:v1"
        )

    async def reserve(self) -> bool:
        """Create the filter, returning False when it already existed."""
        try:
            await self._redis.cf().create(
                self._key,
                self._capacity,
                expansion=self._expansion,
                bucket_size=self._bucket_size,
                max_iterations=self._max_iterations,
            )
        except ResponseError as error:
            if "exists" in str(error).lower():
                return False
            raise
        return True

    async def add(self, item: str) -> bool:
        return await self.add_many([item]) == 1

    async def add_many(self, items: Sequence[str]) -> int:
        if not items:
            return 0
        # CF.INSERT cannot carry bucket size or expansion, so a filter it creates
        # implicitly would take module defaults. Callers reserve() first.
        added = await self._redis.cf().insert(
            self._key, list(items), capacity=self._capacity
        )
        return sum(1 for result in added if result == 1)

    async def contains(self, item: str) -> bool:
        return await self._redis.cf().exists(self._key, item) == 1

    async def contains_if_ready(self, item: str) -> bool | None:
        """Look the item up, or return None when the filter is not trustworthy.

        A filter that has never been marked ready is absent or half-built, and
        would report misses for items it simply has not been given yet.
        """
        pipe = self._redis.pipeline(transaction=False)
        pipe.get(self._meta_key)
        pipe.cf().exists(self._key, item)
        marker, hit = await pipe.execute()
        if marker != self.fingerprint:
            return None
        return bool(hit == 1)

    async def is_ready(self) -> bool:
        return await self._redis.get(self._meta_key) == self.fingerprint

    async def mark_ready(self) -> None:
        """Publish that the key now holds a complete filter."""
        await self._redis.set(self._meta_key, self.fingerprint)

    async def mark_unready(self) -> None:
        await self._redis.delete(self._meta_key)

    async def forget(self, item: str) -> bool:
        """Remove an item that is known to have been inserted.

        Deleting an item that was never inserted can drop another item's
        fingerprint and turn a later lookup into a false negative.
        """
        return await self._redis.cf().delete(self._key, item) == 1

    async def key_exists(self) -> bool:
        """Report whether the key is present, without needing the module."""
        return await self._redis.exists(self._key) == 1

    async def inserted_count(self) -> int:
        """Return live item count, or 0 when the filter does not exist."""
        try:
            info = await self._redis.cf().info(self._key)
        except ResponseError:
            return 0
        inserted = self._info_field(info, "insertedNum", "Number of items inserted")
        deleted = self._info_field(info, "deletedNum", "Number of items deleted")
        return max(inserted - deleted, 0)

    async def drop(self) -> None:
        # Deliberately not named close/aclose: the IoC scope disposes instances by
        # calling those names, and this object holds the shared Redis client.
        await self._redis.delete(self._key, self._meta_key)

    @staticmethod
    def _info_field(info: object, attribute: str, label: str) -> int:
        # CF.INFO answers with a CFInfo under RESP2 and a mapping under RESP3, and
        # CFInfo's fields are untyped, so both shapes are read defensively.
        raw = cast(Any, info)
        value = (
            raw.get(label) if isinstance(raw, dict) else getattr(raw, attribute, None)
        )
        return value if isinstance(value, int) else 0
