from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import jwt
import pytest

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_model import OAuthIdentity, User
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.availability.email_filter import EmailFilter
from src.features.auth.oauth.oauth_service import OAuthService
from src.features.auth.token.token_service import TokenService
from src.shared.exceptions import BadRequestError, UnauthorizedError
from test.test_auth_unit import FakeRedis


class FakeOAuthRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, **_: object) -> bool:
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)


class FakeOAuthRepository:
    def __init__(self, user: User | None = None) -> None:
        self.user = user
        self.identity: OAuthIdentity | None = None
        self.identities_added: list[tuple[User, str, str]] = []
        self.commits = 0

    async def by_identity(self, _: str, __: str) -> OAuthIdentity | None:
        return self.identity

    async def by_email(self, email: str) -> User | None:
        return self.user if self.user is not None and self.user.email == email else None

    async def by_id(self, user_id: UUID) -> User | None:
        return self.user if self.user is not None and self.user.id == user_id else None

    async def create_user(self, email: str, _: None, verified: bool = False) -> User:
        self.user = User(
            id=uuid4(),
            email=email,
            password_hash=None,
            email_verified_at=None,
        )
        return self.user

    async def add_identity(self, user: User, provider: str, subject: str) -> None:
        self.identities_added.append((user, provider, subject))

    async def commit(self) -> None:
        self.commits += 1


class FakeOAuthTokens:
    def issue_access(self, _: UUID, __: str, verified: bool) -> str:
        return f"access:{verified}"

    async def issue_refresh(self, _: UUID) -> str:
        return "refresh"


class FakeJwksClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def get_signing_key_from_jwt(self, _: str) -> SimpleNamespace:
        if self.error:
            raise self.error
        return SimpleNamespace(key="public-key")


def make_service(
    settings: Settings | None = None,
    repository: FakeOAuthRepository | None = None,
) -> tuple[OAuthService, FakeOAuthRedis, FakeOAuthRepository]:
    redis = FakeOAuthRedis()
    repository = repository or FakeOAuthRepository()
    resolved = settings or Settings(
        auth_jwt_secret="test-secret-with-at-least-thirty-two-characters",
        google_client_id="google-client",
        apple_client_id="apple-client",
        microsoft_client_id="microsoft-client",
        microsoft_tenant_id="tenant-id",
    )
    service = OAuthService(
        cast(AuthRepository, repository),
        cast(TokenService, FakeOAuthTokens()),
        redis,  # type: ignore[arg-type]
        EmailFilter(FakeRedis(), resolved),  # type: ignore[arg-type]
        resolved,
    )
    return service, redis, repository


def install_token_claims(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, object],
    jwks_error: Exception | None = None,
) -> None:
    client = FakeJwksClient(jwks_error)
    monkeypatch.setattr(
        OAuthService, "_jwks_client", classmethod(lambda _cls, _url: client)
    )

    def decode(
        _: str,
        __: object,
        *,
        algorithms: list[str],
        audience: str,
        options: dict[str, bool],
    ) -> dict[str, object]:
        assert algorithms == ["RS256", "ES256"]
        assert audience
        assert options == {"verify_iss": False}
        return claims

    monkeypatch.setattr(jwt, "decode", decode)


async def seed_nonce(redis: FakeOAuthRedis, provider: str, nonce: str) -> None:
    await redis.set(f"auth:oauth-nonce:{provider}:{nonce}", "1")


@pytest.mark.asyncio
async def test_oauth_challenge_rejects_unsupported_and_unconfigured_providers() -> None:
    service, _, _ = make_service()

    with pytest.raises(BadRequestError, match="unsupported"):
        await service.create_challenge("github")

    settings = Settings(
        auth_jwt_secret="test-secret-with-at-least-thirty-two-characters"
    )
    unconfigured, _, _ = make_service(settings)
    with pytest.raises(BadRequestError, match="not configured"):
        await unconfigured.create_challenge("google")


@pytest.mark.asyncio
async def test_oauth_challenge_is_required_and_single_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, redis, _ = make_service()
    claims: dict[str, object] = {
        "iss": "https://accounts.google.com",
        "nonce": "nonce",
        "sub": "subject",
        "email": "user@example.com",
        "email_verified": True,
    }
    install_token_claims(monkeypatch, claims)

    with pytest.raises(UnauthorizedError, match="challenge"):
        await service.login("google", "token", "nonce")

    await seed_nonce(redis, "google", "nonce")
    await service.login("google", "token", "nonce")
    with pytest.raises(UnauthorizedError, match="challenge"):
        await service.login("google", "token", "nonce")


@pytest.mark.asyncio
async def test_provider_signature_failure_consumes_nonce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, redis, _ = make_service()
    install_token_claims(monkeypatch, {}, ValueError("bad signature"))
    await seed_nonce(redis, "google", "nonce")

    with pytest.raises(UnauthorizedError, match="provider token is invalid"):
        await service.login("google", "token", "nonce")
    with pytest.raises(UnauthorizedError, match="challenge"):
        await service.login("google", "token", "nonce")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim_updates",
    [
        {"iss": "https://attacker.example"},
        {"nonce": "different-nonce"},
        {"email_verified": False},
        {"sub": ""},
        {"email": "not-an-email"},
    ],
)
async def test_google_rejects_unverifiable_claims(
    monkeypatch: pytest.MonkeyPatch, claim_updates: dict[str, object]
) -> None:
    service, redis, repository = make_service()
    claims: dict[str, object] = {
        "iss": "accounts.google.com",
        "nonce": "nonce",
        "sub": "subject",
        "email": "user@example.com",
        "email_verified": True,
    }
    claims.update(claim_updates)
    install_token_claims(monkeypatch, claims)
    await seed_nonce(redis, "google", "nonce")

    with pytest.raises(UnauthorizedError):
        await service.login("google", "token", "nonce")

    assert repository.identities_added == []
    assert repository.commits == 0


@pytest.mark.asyncio
async def test_verified_google_email_links_and_verifies_existing_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = User(
        id=uuid4(),
        email="user@example.com",
        password_hash="hashed-password",
        email_verified_at=None,
    )
    repository = FakeOAuthRepository(existing)
    service, redis, _ = make_service(repository=repository)
    claims: dict[str, object] = {
        "iss": "accounts.google.com",
        "nonce": "nonce",
        "sub": "google-subject",
        "email": " USER@EXAMPLE.COM ",
        "email_verified": "true",
    }
    install_token_claims(monkeypatch, claims)
    await seed_nonce(redis, "google", "nonce")

    user, access, refresh = await service.login("google", "token", "nonce")

    assert user is existing
    assert existing.email_verified_at is not None
    assert access == "access:True"
    assert refresh == "refresh"
    assert repository.identities_added == [(existing, "google", "google-subject")]
    assert repository.commits == 1


@pytest.mark.asyncio
async def test_microsoft_accepts_preferred_username_only_for_configured_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, redis, repository = make_service()
    claims: dict[str, object] = {
        "iss": "https://login.microsoftonline.com/tenant-id/v2.0",
        "nonce": "nonce",
        "sub": "microsoft-subject",
        "preferred_username": "user@example.com",
    }
    install_token_claims(monkeypatch, claims)
    await seed_nonce(redis, "microsoft", "nonce")

    user, _, _ = await service.login("microsoft", "token", "nonce")

    assert user.email == "user@example.com"
    assert repository.identities_added == [(user, "microsoft", "microsoft-subject")]
