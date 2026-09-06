from __future__ import annotations

import hashlib
import hmac
import logging

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.availability.email_filter import EmailFilter
from src.features.auth.email_address import normalize_email
from src.infrastructure.redis import RedisRateLimiter
from src.shared.exceptions import ServiceUnavailableError, TooManyRequestsError

logger = logging.getLogger(__name__)


class EmailAvailabilityService:
    """Answer whether an address can be registered, and throttle the callers."""

    def __init__(
        self,
        repository: AuthRepository,
        emails: EmailFilter,
        limiter: RedisRateLimiter,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._emails = emails
        self._limiter = limiter
        self._settings = settings

    async def is_available(self, email: str, client_address: str) -> bool:
        await self.throttle(
            client_address, "availability", self._settings.auth_availability_limit
        )
        address = normalize_email(email)
        if not await self._emails.might_exist(address):
            return True
        return await self._repository.by_email(address) is None

    async def throttle(self, client_address: str, scope: str, limit: int) -> None:
        """Charge one request against the caller's window for this scope."""
        key = f"auth:throttle:{scope}:{self._digest(client_address)}"
        try:
            allowed = await self._limiter.hit(
                key, limit, self._settings.auth_throttle_window_seconds
            )
        except Exception as exc:
            # Unlike the filter, this fails closed: an endpoint that enumerates
            # addresses must not become unthrottled because Redis is down.
            raise ServiceUnavailableError() from exc
        if not allowed:
            # Logged so a misconfigured proxy, which would collapse every caller
            # onto one bucket, is visible rather than silent.
            logger.warning("auth_throttle_exceeded", extra={"scope": scope, "key": key})
            raise TooManyRequestsError()

    def _digest(self, client_address: str) -> str:
        # Hashed so raw addresses never reach Redis and IPv6 colons never appear
        # in key names.
        return hmac.new(
            self._settings.auth_jwt_secret.encode(),
            client_address.encode(),
            hashlib.sha256,
        ).hexdigest()[:32]
