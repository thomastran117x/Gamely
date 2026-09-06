from __future__ import annotations

import asyncio
import logging
import secrets

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.availability.email_filter import build_email_filter
from src.features.auth.email_address import normalize_email

EMAIL_FILTER_LOCK_KEY = "auth:email:filter:rebuild"

# Only the holder of the lock may release it, so a rebuild that overran its TTL
# cannot delete a successor's lock.
_RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

logger = logging.getLogger(__name__)


class EmailFilterWarmer:
    """Rebuild the email filter at startup when it is missing or stale."""

    def __init__(
        self,
        redis: Redis,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._redis = redis
        self._session_factory = session_factory
        self._settings = settings
        self._filter = build_email_filter(redis, settings)

    async def warm(self) -> None:
        """Rebuild when needed. Never raises: the filter is optional."""
        if not self._settings.auth_email_filter_enabled:
            return
        try:
            await asyncio.wait_for(
                self._warm(),
                timeout=self._settings.auth_email_filter_rebuild_timeout_seconds,
            )
        except Exception:
            logger.warning("email_filter_warm_failed", exc_info=True)

    async def _warm(self) -> None:
        # The lock is taken before anything destructive: a replica that dropped
        # the key first could delete a filter another replica was midway through
        # building, which would then be marked ready while missing entries.
        token = secrets.token_urlsafe(16)
        if not await self._redis.set(
            EMAIL_FILTER_LOCK_KEY,
            token,
            ex=self._settings.auth_email_filter_lock_seconds,
            nx=True,
        ):
            logger.info("email_filter_rebuild_skipped_locked")
            return
        try:
            await self._rebuild()
        except Exception:
            # Released so another replica can retry immediately.
            await self._redis.eval(_RELEASE_LOCK, 1, EMAIL_FILTER_LOCK_KEY, token)
            raise
        # Left to expire on success, which throttles repeat rebuilds across a
        # rolling deploy without needing to ask whether one was necessary.

    async def _rebuild(self) -> None:
        # Rebuilt unconditionally rather than when some staleness check fires.
        # A filter can silently lose entries to a snapshot restore, and no cheap
        # comparison proves completeness: a count matching the user total can
        # still hide a missing address behind a phantom left by a rolled-back
        # signup. Dropping first also clears a marker, so a rebuild that fails
        # part-way leaves the filter untrusted rather than answering misses.
        await self._filter.drop()
        await self._filter.reserve()
        total = 0
        async with self._session_factory() as session:
            repository = AuthRepository(session)
            async for batch in repository.iter_email_batches(
                self._settings.auth_email_filter_rebuild_batch_size
            ):
                # Built in place rather than swapped in via RENAME: a swap would
                # discard writes made by concurrent signups during the rebuild.
                await self._filter.add_many([normalize_email(email) for email in batch])
                total += len(batch)
        await self._filter.mark_ready()
        logger.info(
            "email_filter_rebuilt", extra={"emails": total, "key": self._filter.key}
        )
