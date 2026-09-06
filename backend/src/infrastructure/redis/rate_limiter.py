from __future__ import annotations

from redis.asyncio import Redis

# INCR and EXPIRE must be atomic: a crash between them would leave a counter with
# no TTL, blocking that caller permanently.
_FIXED_WINDOW = """
local hits = redis.call('INCR', KEYS[1])
if hits == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return hits
"""


class RedisRateLimiter:
    """Fixed-window request counter shared by throttled endpoints."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record one request, returning False once the window's limit is passed."""
        result = await self._redis.eval(_FIXED_WINDOW, 1, key, window_seconds)
        hits = self._count(result)
        # An unreadable reply fails closed rather than waving the request through.
        return hits is not None and hits <= limit

    @staticmethod
    def _count(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, bytes | str):
            try:
                return int(value)
            except ValueError:
                return None
        return None
