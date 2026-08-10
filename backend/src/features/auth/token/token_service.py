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

_ROTATE_REFRESH = """
local user_id = redis.call('GET', KEYS[1])
if user_id then
  redis.call('DEL', KEYS[1])
  redis.call('SREM', 'auth:sessions:' .. user_id, ARGV[1])
  redis.call('SET', KEYS[2], user_id, 'EX', ARGV[2])
  return {'rotated', user_id}
end
local reused_user_id = redis.call('GET', KEYS[2])
if reused_user_id then
  return {'reused', reused_user_id}
end
return {'missing', ''}
"""


class TokenService:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    def issue_access(self, user_id: UUID, email: str, verified: bool) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": str(user_id),
            "email": email,
            "verified": verified,
            "iss": self._settings.auth_jwt_issuer,
            "aud": self._settings.auth_jwt_audience,
            "iat": now,
            "exp": now + timedelta(minutes=self._settings.auth_access_token_minutes),
        }
        return jwt.encode(claims, self._settings.auth_jwt_secret, algorithm="HS256")

    def decode_access(self, value: str) -> UUID:
        try:
            claims = jwt.decode(
                value,
                self._settings.auth_jwt_secret,
                algorithms=["HS256"],
                issuer=self._settings.auth_jwt_issuer,
                audience=self._settings.auth_jwt_audience,
            )
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
        result = await self._redis.eval(
            _ROTATE_REFRESH,
            2,
            f"auth:refresh:{digest}",
            f"auth:reuse:{digest}",
            digest,
            self._settings.auth_refresh_token_days * 86400,
        )
        status, user_id = self._result_pair(result)
        if status == "reused":
            await self.revoke_all(UUID(user_id))
            raise UnauthorizedError("The refresh token has already been used.")
        if status != "rotated":
            raise UnauthorizedError("The refresh token is invalid or expired.")
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
    def _result_pair(value: object) -> tuple[str, str]:
        if not isinstance(value, list) or len(value) != 2:
            return "missing", ""
        return TokenService._text(value[0]) or "missing", TokenService._text(
            value[1]
        ) or ""

    @staticmethod
    def _text(value: object) -> str | None:
        if isinstance(value, bytes):
            return value.decode()
        return value if isinstance(value, str) else None

    def _digest(self, value: str) -> str:
        return hmac.new(
            self._settings.auth_jwt_secret.encode(), value.encode(), hashlib.sha256
        ).hexdigest()
