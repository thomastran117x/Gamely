from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.features.auth.auth_model import OAuthIdentity, User


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_email(self, email: str) -> User | None:
        return await self._session.scalar(select(User).where(User.email == email))

    async def by_id(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def by_identity(self, provider: str, subject: str) -> OAuthIdentity | None:
        return await self._session.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == provider, OAuthIdentity.subject == subject))

    async def create_user(self, email: str, password_hash: str | None, verified: bool = False) -> User:
        from datetime import UTC, datetime
        user = User(email=email, password_hash=password_hash, email_verified_at=datetime.now(UTC) if verified else None)
        self._session.add(user)
        await self._session.flush()
        return user

    async def add_identity(self, user: User, provider: str, subject: str) -> None:
        self._session.add(OAuthIdentity(user_id=user.id, provider=provider, subject=subject))

    async def commit(self) -> None:
        await self._session.commit()
