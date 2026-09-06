from __future__ import annotations

import logging

from redis.asyncio import Redis

from src.application.environment.environment_manager import Settings
from src.infrastructure.redis import RedisCuckooFilter

EMAIL_FILTER_KEY = "auth:email:filter"
EMAIL_FILTER_META_KEY = "auth:email:filter:meta"

logger = logging.getLogger(__name__)


def build_email_filter(redis: Redis, settings: Settings) -> RedisCuckooFilter:
    return RedisCuckooFilter(
        redis,
        EMAIL_FILTER_KEY,
        EMAIL_FILTER_META_KEY,
        capacity=settings.auth_email_filter_capacity,
        bucket_size=settings.auth_email_filter_bucket_size,
        expansion=settings.auth_email_filter_expansion,
        max_iterations=settings.auth_email_filter_max_iterations,
    )


class EmailFilter:
    """Membership hint for registered addresses that never raises.

    A miss is definitive, so the caller can skip its database lookup. Everything
    else - a hit, a filter that has not been built yet, a Redis outage, a missing
    module, the kill switch - reports True, which means "ask the database",
    exactly as callers behaved before.
    """

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._enabled = settings.auth_email_filter_enabled
        self._filter = build_email_filter(redis, settings)

    async def might_exist(self, email: str) -> bool:
        if not self._enabled:
            return True
        try:
            hit = await self._filter.contains_if_ready(email)
            # None means the filter is absent or mid-rebuild, so it cannot be
            # trusted to distinguish "unknown" from "not registered".
            return True if hit is None else hit
        except Exception:
            logger.warning("email_filter_lookup_failed", exc_info=True)
            return True

    async def remember(self, email: str) -> None:
        if not self._enabled:
            return
        try:
            await self._filter.add(email)
        except Exception:
            logger.warning("email_filter_write_failed", exc_info=True)
