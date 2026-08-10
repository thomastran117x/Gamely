from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from threading import RLock
from typing import ClassVar
from uuid import UUID

import jwt
from redis.asyncio import Redis

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_model import User
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.token.token_service import TokenService
from src.shared.exceptions import BadRequestError, UnauthorizedError


class OAuthService:
    _jwks_clients: ClassVar[dict[str, jwt.PyJWKClient]] = {}
    _jwks_lock: ClassVar[RLock] = RLock()

    def __init__(
        self,
        repository: AuthRepository,
        tokens: TokenService,
        redis: Redis,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._tokens = tokens
        self._redis = redis
        self._settings = settings

    async def create_challenge(self, provider: str) -> str:
        self._provider_config(provider)
        nonce = secrets.token_urlsafe(32)
        await self._redis.set(f"auth:oauth-nonce:{provider}:{nonce}", "1", ex=300)
        return nonce

    async def login(
        self, provider: str, id_token: str, nonce: str
    ) -> tuple[User, str, str]:
        if not await self._redis.delete(f"auth:oauth-nonce:{provider}:{nonce}"):
            raise UnauthorizedError("The OAuth challenge is invalid or expired.")
        client_id, issuers, jwks_url = self._provider_config(provider)
        try:
            key = await asyncio.to_thread(
                self._jwks_client(jwks_url).get_signing_key_from_jwt, id_token
            )
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=["RS256", "ES256"],
                audience=client_id,
                options={"verify_iss": False},
            )
        except Exception as exc:
            raise UnauthorizedError("The provider token is invalid.") from exc
        if str(claims.get("iss", "")) not in issuers or not secrets.compare_digest(
            str(claims.get("nonce", "")), nonce
        ):
            raise UnauthorizedError("The provider token could not be verified.")
        email = self._email_address(self._email_claim(provider, claims))
        if not claims.get("sub") or (
            provider != "microsoft"
            and claims.get("email_verified") not in (True, "true")
        ):
            raise UnauthorizedError(
                "The provider did not supply a verified email address."
            )
        identity = await self._repository.by_identity(provider, str(claims["sub"]))
        if identity is not None:
            user = await self._require_user(identity.user_id)
        else:
            existing_user = await self._repository.by_email(email)
            if existing_user is None:
                user = await self._repository.create_user(email, None, verified=True)
            else:
                user = existing_user
                if user.email_verified_at is None:
                    user.email_verified_at = datetime.now(UTC)
            await self._repository.add_identity(user, provider, str(claims["sub"]))
            await self._repository.commit()
        return (
            user,
            self._tokens.issue_access(
                user.id, user.email, user.email_verified_at is not None
            ),
            await self._tokens.issue_refresh(user.id),
        )

    def _provider_config(self, provider: str) -> tuple[str, set[str], str]:
        if provider == "google":
            return self._configured(
                provider,
                self._settings.google_client_id,
                {"https://accounts.google.com", "accounts.google.com"},
                "https://www.googleapis.com/oauth2/v3/certs",
            )
        if provider == "apple":
            return self._configured(
                provider,
                self._settings.apple_client_id,
                {"https://appleid.apple.com"},
                "https://appleid.apple.com/auth/keys",
            )
        if provider == "microsoft":
            tenant = self._settings.microsoft_tenant_id
            if not tenant:
                raise BadRequestError("The OAuth provider is not configured.")
            issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
            return self._configured(
                provider,
                self._settings.microsoft_client_id,
                {issuer},
                f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys",
            )
        raise BadRequestError("The OAuth provider is unsupported.")

    @staticmethod
    def _configured(
        provider: str, client_id: str, issuers: set[str], jwks_url: str
    ) -> tuple[str, set[str], str]:
        if not client_id:
            raise BadRequestError(f"The {provider} OAuth provider is not configured.")
        return client_id, issuers, jwks_url

    @classmethod
    def _jwks_client(cls, url: str) -> jwt.PyJWKClient:
        with cls._jwks_lock:
            if url not in cls._jwks_clients:
                cls._jwks_clients[url] = jwt.PyJWKClient(url)
            return cls._jwks_clients[url]

    @staticmethod
    def _email_claim(provider: str, claims: dict[str, object]) -> str:
        if provider == "microsoft":
            return str(claims.get("email") or claims.get("preferred_username") or "")
        return str(claims.get("email", ""))

    async def _require_user(self, user_id: UUID) -> User:
        user = await self._repository.by_id(user_id)
        if user is None:
            raise UnauthorizedError()
        return user

    @staticmethod
    def _email_address(value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or len(email) > 320:
            raise UnauthorizedError(
                "The provider did not supply a valid email address."
            )
        return email
