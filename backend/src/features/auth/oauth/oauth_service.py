from __future__ import annotations

import asyncio
import secrets
from uuid import UUID

import jwt
from redis.asyncio import Redis

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_model import User
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.token.token_service import TokenService
from src.shared.exceptions import BadRequestError, UnauthorizedError


class OAuthService:
    def __init__(self, repository: AuthRepository, tokens: TokenService, redis: Redis, settings: Settings) -> None:
        self._repository = repository
        self._tokens = tokens
        self._redis = redis
        self._settings = settings

    async def create_challenge(self, provider: str) -> str:
        self._provider_config(provider)
        nonce = secrets.token_urlsafe(32)
        await self._redis.set(f"auth:oauth-nonce:{provider}:{nonce}", "1", ex=300)
        return nonce

    async def login(self, provider: str, id_token: str, nonce: str) -> tuple[User, str, str]:
        if not await self._redis.delete(f"auth:oauth-nonce:{provider}:{nonce}"):
            raise UnauthorizedError("The OAuth challenge is invalid or expired.")
        client_id, issuer, jwks_url = self._provider_config(provider)
        try:
            key = await asyncio.to_thread(jwt.PyJWKClient(jwks_url).get_signing_key_from_jwt, id_token)
            claims = jwt.decode(id_token, key.key, algorithms=["RS256", "ES256"], audience=client_id, options={"verify_iss": False})
        except Exception as exc:
            raise UnauthorizedError("The provider token is invalid.") from exc
        token_issuer = str(claims.get("iss", ""))
        valid_issuer = token_issuer.startswith("https://login.microsoftonline.com/") and token_issuer.endswith("/v2.0") if provider == "microsoft" else token_issuer == issuer
        if not valid_issuer or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise UnauthorizedError("The provider token could not be verified.")
        email = self._email_address(str(claims.get("email", "")))
        if claims.get("email_verified") not in (True, "true") or not claims.get("sub"):
            raise UnauthorizedError("The provider did not supply a verified email address.")
        identity = await self._repository.by_identity(provider, str(claims["sub"]))
        if identity is not None:
            user = await self._require_user(identity.user_id)
        else:
            user = await self._repository.by_email(email) or await self._repository.create_user(email, None, verified=True)
            await self._repository.add_identity(user, provider, str(claims["sub"]))
            await self._repository.commit()
        return user, self._tokens.issue_access(user.id, user.email, user.email_verified_at is not None), await self._tokens.issue_refresh(user.id)

    def _provider_config(self, provider: str) -> tuple[str, str, str]:
        configs = {
            "google": (self._settings.google_client_id, "https://accounts.google.com", "https://www.googleapis.com/oauth2/v3/certs"),
            "microsoft": (self._settings.microsoft_client_id, "", "https://login.microsoftonline.com/common/discovery/v2.0/keys"),
            "apple": (self._settings.apple_client_id, "https://appleid.apple.com", "https://appleid.apple.com/auth/keys"),
        }
        if provider not in configs:
            raise BadRequestError("The OAuth provider is unsupported.")
        config = configs[provider]
        if not config[0]:
            raise BadRequestError("The OAuth provider is not configured.")
        return config

    async def _require_user(self, user_id: UUID) -> User:
        user = await self._repository.by_id(user_id)
        if user is None:
            raise UnauthorizedError()
        return user

    @staticmethod
    def _email_address(value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or len(email) > 320:
            raise UnauthorizedError("The provider did not supply a valid email address.")
        return email
