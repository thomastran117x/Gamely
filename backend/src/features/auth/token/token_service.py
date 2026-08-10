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
local session = redis.call('GET', KEYS[1])
if session then
  redis.call('DEL', KEYS[1])
  redis.call('SET', KEYS[2], session, 'EX', ARGV[1])
  return {'rotated', session}
end
local reused_session = redis.call('GET', KEYS[2])
if reused_session then
  redis.call('DEL', KEYS[2])
  return {'reused', reused_session}
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

    async def issue_refresh(self, user_id: UUID, family_id: str | None = None) -> str:
        value = secrets.token_urlsafe(48)
        digest = self._digest(value)
        family_id = family_id or secrets.token_urlsafe(24)
        ttl = timedelta(days=self._settings.auth_refresh_token_days)
        family_key = self._family_key(user_id, family_id)
        pipe = self._redis.pipeline()
        pipe.set(
            f"auth:refresh:{digest}", self._session_value(user_id, family_id), ex=ttl
        )
        pipe.sadd(family_key, digest)
        pipe.expire(family_key, ttl)
        pipe.sadd(f"auth:sessions:{user_id}", family_id)
        pipe.expire(f"auth:sessions:{user_id}", ttl)
        await pipe.execute()
        return value

    async def rotate_refresh(self, value: str) -> tuple[UUID, str]:
        digest = self._digest(value)
        result = await self._redis.eval(
            _ROTATE_REFRESH,
            2,
            f"auth:refresh:{digest}",
            f"auth:reuse:{digest}",
            self._settings.auth_refresh_token_days * 86400,
        )
        status, session = self._result_pair(result)
        if status == "missing":
            raise UnauthorizedError("The refresh token is invalid or expired.")
        user_id, family_id = self._parse_session(session)
        if status == "reused":
            await self._revoke_family(user_id, family_id)
            raise UnauthorizedError("The refresh token has already been used.")
        if status != "rotated":
            raise UnauthorizedError("The refresh token is invalid or expired.")
        return user_id, family_id

    async def revoke_refresh(self, value: str) -> None:
        digest = self._digest(value)
        session = self._text(await self._redis.get(f"auth:refresh:{digest}"))
        if session is not None:
            await self._revoke_family(*self._parse_session(session))

    async def revoke_all(self, user_id: UUID) -> None:
        key = f"auth:sessions:{user_id}"
        family_ids = await self._redis.smembers(key)
        for family_id in family_ids:
            family_text = self._text(family_id)
            if family_text is not None:
                await self._revoke_family(user_id, family_text)
        await self._redis.delete(key)

    async def _revoke_family(self, user_id: UUID, family_id: str) -> None:
        family_key = self._family_key(user_id, family_id)
        digests = await self._redis.smembers(family_key)
        pipe = self._redis.pipeline()
        for digest in digests:
            digest_text = self._text(digest)
            if digest_text is not None:
                pipe.delete(f"auth:refresh:{digest_text}")
                pipe.delete(f"auth:reuse:{digest_text}")
        pipe.delete(family_key)
        pipe.srem(f"auth:sessions:{user_id}", family_id)
        await pipe.execute()

    @staticmethod
    def _family_key(user_id: UUID, family_id: str) -> str:
        return f"auth:session:{user_id}:{family_id}"

    @staticmethod
    def _session_value(user_id: UUID, family_id: str) -> str:
        return f"{user_id}:{family_id}"

    @staticmethod
    def _parse_session(value: str) -> tuple[UUID, str]:
        try:
            user_id, family_id = value.split(":", 1)
            if not family_id:
                raise ValueError
            return UUID(user_id), family_id
        except ValueError as exc:
            raise UnauthorizedError("The refresh token is invalid or expired.") from exc

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
