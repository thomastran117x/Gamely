from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import aio_pika
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.contracts import ApplicationServices
from src.application.environment.environment_manager import Settings
from src.infrastructure.database import create_session_factory
from src.infrastructure.opensearch import create_opensearch_client
from src.infrastructure.rabbitmq import connect_rabbitmq
from src.infrastructure.redis import create_redis_client
from src.infrastructure.s3 import S3Client, create_s3_session


class InfrastructureServices(ApplicationServices):
    """Long-lived clients owned by the FastAPI application lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine, self.session_factory = create_session_factory(
            settings.database_url
        )
        self.redis = create_redis_client(settings.redis_url)
        self.opensearch = create_opensearch_client(settings.opensearch_url)
        self.rabbitmq: aio_pika.abc.AbstractRobustConnection | None = None
        self.s3_session = create_s3_session(settings.aws_region)

    async def connect(self) -> None:
        self.rabbitmq = await connect_rabbitmq(self.settings.rabbitmq_url)

    async def close(self) -> None:
        if self.rabbitmq is not None:
            await self.rabbitmq.close()
        await self.opensearch.close()
        await self.redis.aclose()
        await self.engine.dispose()

    async def ready(self) -> None:
        """Raise when any local runtime dependency is unavailable."""
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await self.redis.ping()
        if not await self.opensearch.ping():
            raise RuntimeError("OpenSearch is unavailable")
        if self.rabbitmq is None or self.rabbitmq.is_closed:
            raise RuntimeError("RabbitMQ connection is unavailable")
        channel = await self.rabbitmq.channel()
        await channel.close()

    @asynccontextmanager
    async def s3_client(self) -> AsyncIterator[S3Client]:
        """Yield an AWS S3 client when an S3-using feature needs one."""
        async with self.s3_session.client("s3") as client:
            yield client

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            yield session
