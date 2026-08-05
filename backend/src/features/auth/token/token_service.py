from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from redis.asyncio import Redis

from src.application.environment.environment_manager import Settings
from src.shared.exceptions import UnauthorizedError


class TokenService:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    def issue_access(self, user_id: UUID, email: str, verified: bool) -> str:
        now = datetime.now(UTC)
        return jwt.encode({"sub": str(user_id), "email": email, "verified": verified, "iss": self._settings.auth_jwt_issuer, "aud": self._settings.auth_jwt_audience, "iat": now, "exp": now + timedelta(minutes=self._settings.auth_access_token_minutes)}, self._settings.auth_jwt_secret, algorithm="HS256")

    def decode_access(self, value: str) -> UUID:
        try:
            claims = jwt.decode(value, self._settings.auth_jwt_secret, algorithms=["HS256"], issuer=self._settings.auth_jwt_issuer, audience=self._settings.auth_jwt_audience)
            return UUID(str(claims["sub"]))
        except (jwt.PyJWTError, KeyError, ValueError) as exc:
            raise UnauthorizedError("The access token is invalid or expired.") from exc

    async def issue_refresh(self, user_id: UUID) -> str:
        value = secrets.token_urlsafe(48)
        digest = self._digest(value)
        ttl = timedelta(days=self._settings.auth_refresh_token_days)
        pipe = self._redis.pipeline()
        pipe.set(f"auth:refresh:{digest}", str(user_id), ex=ttl)
        pipe.sadd(f"auth:sessions:{user_id}", digest)
        pipe.expire(f"auth:sessions:{user_id}", ttl)
        await pipe.execute()
        return value

    async def rotate_refresh(self, value: str) -> UUID:
        digest = self._digest(value)
        user_id = self._text(await self._redis.get(f"auth:refresh:{digest}"))
        if user_id is None:
            raise UnauthorizedError("The refresh token is invalid or expired.")
        pipe = self._redis.pipeline()
        pipe.delete(f"auth:refresh:{digest}")
        pipe.srem(f"auth:sessions:{user_id}", digest)
        pipe.set(f"auth:reuse:{digest}", "1", ex=timedelta(days=self._settings.auth_refresh_token_days))
        await pipe.execute()
        return UUID(user_id)

    async def revoke_refresh(self, value: str) -> None:
        digest = self._digest(value)
        user_id = self._text(await self._redis.get(f"auth:refresh:{digest}"))
        if user_id is not None:
            await self._redis.delete(f"auth:refresh:{digest}")
            await self._redis.srem(f"auth:sessions:{user_id}", digest)

    async def revoke_all(self, user_id: UUID) -> None:
        key = f"auth:sessions:{user_id}"
        digests = await self._redis.smembers(key)
        pipe = self._redis.pipeline()
        for digest in digests:
            digest_text = self._text(digest)
            if digest_text is not None:
                pipe.delete(f"auth:refresh:{digest_text}")
        pipe.delete(key)
        await pipe.execute()

    @staticmethod
    def _text(value: str | bytes | None) -> str | None:
        return value.decode() if isinstance(value, bytes) else value

    def _digest(self, value: str) -> str:
        return hmac.new(self._settings.auth_jwt_secret.encode(), value.encode(), hashlib.sha256).hexdigest()