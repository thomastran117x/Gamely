from __future__ import annotations

import hashlib
from typing import cast
from uuid import UUID, uuid4

import pytest

from src.application.environment.environment_manager import Settings
from redis.exceptions import ResponseError
from sqlalchemy.exc import IntegrityError

from src.features.auth.auth_model import User
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.auth_service import AuthService
from src.features.auth.availability.email_filter import EmailFilter
from src.features.auth.token.token_service import TokenService
from src.infrastructure.email import EmailJob, EmailPublisher
from src.shared.exceptions import BadRequestError, ConflictError, UnauthorizedError
from test.test_auth_unit import FakeRedis, mark_filter_ready


class FakeRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self.users = {user.email: user for user in users or []}
        self.commits = 0
        self.rollbacks = 0
        self.by_email_calls = 0
        self.create_error: Exception | None = None

    async def by_email(self, email: str) -> User | None:
        self.by_email_calls += 1
        return self.users.get(email)

    async def by_id(self, user_id: UUID) -> User | None:
        return next((user for user in self.users.values() if user.id == user_id), None)

    async def create_user(
        self, email: str, password_hash: str | None, verified: bool = False
    ) -> User:
        if self.create_error is not None:
            raise self.create_error
        if email in self.users:
            # Model the unique index on users.email: it, not the filter, is what
            # ultimately rejects a duplicate.
            raise IntegrityError("INSERT INTO users", (), Exception("duplicate"))
        user = User(
            id=uuid4(),
            email=email,
            password_hash=password_hash,
            email_verified_at=None,
        )
        self.users[email] = user
        return user

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeTokenService:
    def __init__(self) -> None:
        self.revoked_users: list[UUID] = []

    def issue_access(self, _: UUID, __: str, ___: bool) -> str:
        return "access"

    async def issue_refresh(self, _: UUID, __: str | None = None) -> str:
        return "refresh"

    async def revoke_all(self, user_id: UUID) -> None:
        self.revoked_users.append(user_id)


class FakeEmailPublisher:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.jobs: list[EmailJob] = []

    async def publish(self, job: EmailJob) -> None:
        if self.error:
            raise self.error
        self.jobs.append(job)


class FakePasswords:
    def hash(self, value: str) -> str:
        return f"hashed:{value}"

    def verify(self, value: str, hashed: str) -> bool:
        return hashed == f"hashed:{value}"


def make_user(
    email: str = "user@example.com",
    password_hash: str | None = "hashed:correct-password",
) -> User:
    return User(
        id=uuid4(),
        email=email,
        password_hash=password_hash,
        email_verified_at=None,
    )


def make_service(
    repository: FakeRepository | None = None,
    redis: FakeRedis | None = None,
    email: FakeEmailPublisher | None = None,
    tokens: FakeTokenService | None = None,
    settings: Settings | None = None,
) -> tuple[
    AuthService,
    FakeRepository,
    FakeRedis,
    FakeEmailPublisher,
    FakeTokenService,
    EmailFilter,
]:
    repository = repository or FakeRepository()
    redis = redis or FakeRedis()
    email = email or FakeEmailPublisher()
    tokens = tokens or FakeTokenService()
    settings = settings or Settings(
        auth_jwt_secret="test-secret-with-at-least-thirty-two-characters"
    )
    mark_filter_ready(redis, settings)
    emails = EmailFilter(redis, settings)  # type: ignore[arg-type]
    service = AuthService(
        cast(AuthRepository, repository),
        cast(TokenService, tokens),
        redis,  # type: ignore[arg-type]
        cast(EmailPublisher, email),
        emails,
        settings,
    )
    service._passwords = FakePasswords()  # type: ignore[assignment]
    return service, repository, redis, email, tokens, emails


@pytest.mark.asyncio
@pytest.mark.parametrize("email", ["missing-at-sign", "x" * 321])
async def test_signup_rejects_invalid_email(email: str) -> None:
    service, repository, _, email_publisher, _, _ = make_service()

    with pytest.raises(BadRequestError, match="valid email"):
        await service.signup(email, "a-secure-password")

    assert repository.users == {}
    assert email_publisher.jobs == []


@pytest.mark.asyncio
async def test_signup_rejects_short_password() -> None:
    service, repository, _, _, _, _ = make_service()

    with pytest.raises(BadRequestError, match="at least 12"):
        await service.signup("user@example.com", "too-short")

    assert repository.users == {}


@pytest.mark.asyncio
async def test_signup_normalizes_email_and_rejects_duplicate() -> None:
    existing = make_user()
    service, repository, _, _, _, _ = make_service(FakeRepository([existing]))

    with pytest.raises(ConflictError):
        await service.signup("  USER@EXAMPLE.COM ", "a-secure-password")

    assert list(repository.users) == ["user@example.com"]


@pytest.mark.asyncio
async def test_signup_rolls_back_code_and_user_transaction_on_publish_failure() -> None:
    publisher = FakeEmailPublisher(ConnectionError("RabbitMQ unavailable"))
    service, repository, redis, _, _, _ = make_service(email=publisher)

    with pytest.raises(ConnectionError):
        await service.signup("user@example.com", "a-secure-password")

    assert repository.commits == 0
    assert repository.rollbacks == 1
    assert not [key for key in redis.values if key.startswith("auth:code:")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user,password",
    [
        (None, "correct-password"),
        (make_user(password_hash=None), "correct-password"),
        (make_user(), "wrong-password"),
    ],
)
async def test_login_rejects_unknown_social_only_and_wrong_password_accounts(
    user: User | None, password: str
) -> None:
    repository = FakeRepository([user] if user is not None else None)
    service, _, _, _, _, _ = make_service(repository)

    with pytest.raises(UnauthorizedError, match="email address or password"):
        await service.login("user@example.com", password)


@pytest.mark.asyncio
async def test_code_cooldown_prevents_duplicate_delivery() -> None:
    service, _, _, publisher, _, _ = make_service()

    await service._send_code("user@example.com", "verify")
    with pytest.raises(BadRequestError, match="Please wait"):
        await service._send_code("user@example.com", "verify")

    assert len(publisher.jobs) == 1


@pytest.mark.asyncio
async def test_unknown_email_and_delivery_failure_are_silent_for_enumeration_routes() -> (
    None
):
    unknown_service, _, _, unknown_publisher, _, _ = make_service()
    await unknown_service.send_verification("unknown@example.com")
    await unknown_service.forgot_password("unknown@example.com")

    user = make_user()
    failing_publisher = FakeEmailPublisher(ConnectionError("broker unavailable"))
    known_service, _, redis, _, _, _ = make_service(
        FakeRepository([user]), email=failing_publisher
    )
    await known_service.send_verification(user.email)
    await known_service.forgot_password(user.email)

    assert unknown_publisher.jobs == []
    assert not [key for key in redis.values if key.startswith("auth:code:")]


@pytest.mark.asyncio
async def test_code_is_deleted_after_five_failed_attempts() -> None:
    service, _, redis, _, _, _ = make_service()
    key = "auth:code:reset:user@example.com"
    digest = hashlib.sha256(b"correct").hexdigest()
    await redis.set(key, f"{digest}:3")

    assert await service._consume_code("user@example.com", "reset", "wrong") is False
    assert await redis.get(key) == f"{digest}:4"
    assert await service._consume_code("user@example.com", "reset", "wrong") is False
    assert await redis.get(key) is None


@pytest.mark.asyncio
async def test_reset_password_rejects_invalid_code_without_mutation() -> None:
    user = make_user()
    service, repository, _, _, tokens, _ = make_service(FakeRepository([user]))

    with pytest.raises(BadRequestError, match="invalid or expired"):
        await service.reset_password(user.email, "123456", "replacement-password")

    assert user.password_hash == "hashed:correct-password"
    assert repository.commits == 0
    assert tokens.revoked_users == []


@pytest.mark.asyncio
async def test_reset_password_hashes_password_and_revokes_all_sessions() -> None:
    user = make_user()
    service, repository, redis, _, tokens, _ = make_service(FakeRepository([user]))
    digest = hashlib.sha256(b"123456").hexdigest()
    await redis.set(f"auth:code:reset:{user.email}", f"{digest}:0")

    await service.reset_password(user.email, "123456", "replacement-password")

    assert user.password_hash == "hashed:replacement-password"
    assert repository.commits == 1
    assert tokens.revoked_users == [user.id]


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password() -> None:
    user = make_user()
    service, repository, _, _, tokens, _ = make_service(FakeRepository([user]))

    with pytest.raises(UnauthorizedError, match="current password"):
        await service.change_password(user.id, "wrong-password", "replacement-password")

    assert user.password_hash == "hashed:correct-password"
    assert repository.commits == 0
    assert tokens.revoked_users == []


@pytest.mark.asyncio
async def test_change_password_updates_hash_and_revokes_all_sessions() -> None:
    user = make_user()
    service, repository, _, _, tokens, _ = make_service(FakeRepository([user]))

    await service.change_password(user.id, "correct-password", "replacement-password")

    assert user.password_hash == "hashed:replacement-password"
    assert repository.commits == 1
    assert tokens.revoked_users == [user.id]


@pytest.mark.asyncio
async def test_signup_skips_the_database_lookup_when_the_filter_misses() -> None:
    service, repository, redis, _, _, _ = make_service()

    await service.signup("fresh@example.com", "a-secure-password")

    assert repository.by_email_calls == 0
    assert repository.commits == 1
    assert redis.filters["auth:email:filter"] == {"fresh@example.com"}


@pytest.mark.asyncio
async def test_signup_consults_the_database_on_a_false_positive() -> None:
    redis = FakeRedis()
    redis.false_positives.add("fresh@example.com")
    service, repository, _, _, _, _ = make_service(redis=redis)

    await service.signup("fresh@example.com", "a-secure-password")

    assert repository.by_email_calls == 1
    assert repository.commits == 1


@pytest.mark.asyncio
async def test_signup_rejects_a_duplicate_the_filter_knows_about() -> None:
    existing = make_user()
    service, repository, _, _, _, emails = make_service(FakeRepository([existing]))
    await emails.remember(existing.email)

    with pytest.raises(ConflictError):
        await service.signup(existing.email, "a-secure-password")

    assert repository.by_email_calls == 1


@pytest.mark.asyncio
async def test_signup_repairs_the_filter_when_the_database_rejects_a_duplicate() -> (
    None
):
    existing = make_user()
    # The filter is empty, so signup takes the fast path and the unique index is
    # what rejects the address.
    service, repository, redis, _, _, _ = make_service(FakeRepository([existing]))

    with pytest.raises(ConflictError):
        await service.signup(existing.email, "a-secure-password")

    assert repository.by_email_calls == 0
    assert repository.rollbacks == 1
    assert redis.filters["auth:email:filter"] == {existing.email}


@pytest.mark.asyncio
async def test_signup_stores_the_normalized_address_in_the_filter() -> None:
    service, _, redis, _, _, _ = make_service()

    await service.signup("  User@Example.COM ", "a-secure-password")

    assert redis.filters["auth:email:filter"] == {"user@example.com"}


@pytest.mark.asyncio
async def test_signup_falls_back_to_the_database_when_the_filter_fails() -> None:
    redis = FakeRedis()
    redis.filter_error = ResponseError("unknown command 'CF.EXISTS'")
    service, repository, _, _, _, _ = make_service(redis=redis)

    await service.signup("fresh@example.com", "a-secure-password")

    assert repository.by_email_calls == 1
    assert repository.commits == 1


@pytest.mark.asyncio
async def test_signup_bypasses_the_filter_when_it_is_disabled() -> None:
    settings = Settings(
        auth_jwt_secret="test-secret-with-at-least-thirty-two-characters",
        auth_email_filter_enabled=False,
    )
    service, repository, redis, _, _, _ = make_service(settings=settings)

    await service.signup("fresh@example.com", "a-secure-password")

    assert repository.by_email_calls == 1
    assert redis.filters["auth:email:filter"] == set()


@pytest.mark.asyncio
async def test_signup_remembers_the_address_before_the_verification_email() -> None:
    publisher = FakeEmailPublisher(ConnectionError("RabbitMQ unavailable"))
    service, repository, redis, _, _, _ = make_service(email=publisher)

    with pytest.raises(ConnectionError):
        await service.signup("fresh@example.com", "a-secure-password")

    # A phantom entry after a rollback only costs a later lookup; a lost entry
    # would let the availability endpoint answer "available" for a taken address.
    assert repository.rollbacks == 1
    assert redis.filters["auth:email:filter"] == {"fresh@example.com"}
