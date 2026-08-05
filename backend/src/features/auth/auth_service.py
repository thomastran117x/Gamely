from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pwdlib import PasswordHash
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_model import User
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.token.token_service import TokenService
from src.infrastructure.email import EmailJob, EmailPublisher
from src.shared.exceptions import BadRequestError, ConflictError, UnauthorizedError


class AuthService:
    def __init__(self, repository: AuthRepository, tokens: TokenService, redis: Redis, email: EmailPublisher, settings: Settings) -> None:
        self._repository, self._tokens, self._redis, self._email, self._settings = repository, tokens, redis, email, settings
        self._passwords = PasswordHash.recommended()

    async def signup(self, email: str, password: str) -> None:
        email = self._email_address(email)
        self._check_password(password)
        if await self._repository.by_email(email):
            raise ConflictError("An account already exists for this email address.")
        try:
            await self._repository.create_user(email, await asyncio.to_thread(self._passwords.hash, password))
            await self._send_code(email, "verify")
            await self._repository.commit()
        except IntegrityError as exc:
            await self._repository.rollback()
            raise ConflictError("An account already exists for this email address.") from exc
        except Exception:
            await self._repository.rollback()
            raise

    async def login(self, email: str, password: str) -> tuple[User, str, str]:
        user = await self._repository.by_email(self._email_address(email))
        valid = user is not None and user.password_hash is not None and await asyncio.to_thread(self._passwords.verify, password, user.password_hash)
        if not valid or user is None:
            raise UnauthorizedError("The email address or password is incorrect.")
        return user, self._tokens.issue_access(user.id, user.email, user.email_verified_at is not None), await self._tokens.issue_refresh(user.id)

    async def send_verification(self, email: str) -> None:
        user = await self._repository.by_email(self._email_address(email))
        if user is not None and user.email_verified_at is None:
            try:
                await self._send_code(user.email, "verify")
            except Exception:
                return

    async def verify_email(self, email: str, code: str) -> None:
        user = await self._repository.by_email(self._email_address(email))
        if user is None or not await self._consume_code(user.email, "verify", code):
            raise BadRequestError("The verification code is invalid or expired.")
        user.email_verified_at = datetime.now(UTC)
        await self._repository.commit()

    async def forgot_password(self, email: str) -> None:
        user = await self._repository.by_email(self._email_address(email))
        if user is not None and user.password_hash is not None:
            try:
                await self._send_code(user.email, "reset")
            except Exception:
                return

    async def reset_password(self, email: str, code: str, password: str) -> None:
        self._check_password(password)
        user = await self._repository.by_email(self._email_address(email))
        if user is None or not await self._consume_code(user.email, "reset", code):
            raise BadRequestError("The reset code is invalid or expired.")
        user.password_hash = await asyncio.to_thread(self._passwords.hash, password)
        await self._repository.commit()
        await self._tokens.revoke_all(user.id)

    async def change_password(self, user_id: UUID, current: str, new: str) -> None:
        self._check_password(new)
        user = await self._require_user(user_id)
        valid = user.password_hash is not None and await asyncio.to_thread(self._passwords.verify, current, user.password_hash)
        if not valid:
            raise UnauthorizedError("The current password is incorrect.")
        user.password_hash = await asyncio.to_thread(self._passwords.hash, new)
        await self._repository.commit()
        await self._tokens.revoke_all(user.id)

    async def refresh(self, value: str) -> tuple[User, str, str]:
        user = await self._require_user(await self._tokens.rotate_refresh(value))
        return user, self._tokens.issue_access(user.id, user.email, user.email_verified_at is not None), await self._tokens.issue_refresh(user.id)

    async def _require_user(self, user_id: UUID) -> User:
        user = await self._repository.by_id(user_id)
        if user is None:
            raise UnauthorizedError()
        return user

    async def _send_code(self, email: str, purpose: str) -> None:
        cooldown = f"auth:code:cooldown:{purpose}:{email}"
        code_key = f"auth:code:{purpose}:{email}"
        if not await self._redis.set(cooldown, "1", ex=60, nx=True):
            raise BadRequestError("Please wait before requesting another code.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = datetime.now(UTC) + timedelta(minutes=self._settings.auth_code_minutes)
        payload = f"{hashlib.sha256(code.encode()).hexdigest()}:0"
        try:
            await self._redis.set(code_key, payload, ex=timedelta(minutes=self._settings.auth_code_minutes))
            await self._email.publish(EmailJob(email, purpose, code, expires_at))
        except Exception:
            await self._redis.delete(cooldown, code_key)
            raise

    async def _consume_code(self, email: str, purpose: str, code: str) -> bool:
        key = f"auth:code:{purpose}:{email}"
        stored = self._text(await self._redis.get(key))
        if stored is None:
            return False
        digest, attempts = stored.split(":", 1)
        if int(attempts) >= 4:
            await self._redis.delete(key)
            return False
        if not secrets.compare_digest(digest, hashlib.sha256(code.encode()).hexdigest()):
            ttl = await self._redis.ttl(key)
            await self._redis.set(key, f"{digest}:{int(attempts) + 1}", ex=max(ttl, 1))
            return False
        await self._redis.delete(key)
        return True

    @staticmethod
    def _text(value: str | bytes | None) -> str | None:
        return value.decode() if isinstance(value, bytes) else value

    @staticmethod
    def _email_address(value: str) -> str:
        email = value.strip().lower()
        if "@" not in email or len(email) > 320:
            raise BadRequestError("A valid email address is required.")
        return email

    @staticmethod
    def _check_password(value: str) -> None:
        if len(value) < 12:
            raise BadRequestError("Passwords must be at least 12 characters long.")
