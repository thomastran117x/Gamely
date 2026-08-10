from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import uuid4

from src.shared.exceptions import ServiceUnavailableError

import aio_pika

EXCHANGE = "auth.email"
QUEUE = "auth.email.primary"
DEAD_QUEUE = "auth.email.dead"


@dataclass(frozen=True)
class EmailJob:
    recipient: str
    template: str
    code: str
    expires_at: datetime
    attempts: int = 0
    id: str = ""

    def to_bytes(self) -> bytes:
        data = asdict(self)
        data["expires_at"] = self.expires_at.isoformat()
        data["id"] = self.id or str(uuid4())
        return json.dumps(data).encode()

    @classmethod
    def from_bytes(cls, body: bytes) -> EmailJob:
        data = json.loads(body)
        return cls(**{**data, "expires_at": datetime.fromisoformat(data["expires_at"])})


async def declare_topology(
    channel: aio_pika.abc.AbstractChannel,
) -> aio_pika.abc.AbstractExchange:
    exchange = await channel.declare_exchange(
        EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True
    )
    dead = await channel.declare_queue(DEAD_QUEUE, durable=True)
    await dead.bind(exchange, "dead")
    primary = await channel.declare_queue(
        QUEUE,
        durable=True,
        arguments={
            "x-dead-letter-exchange": EXCHANGE,
            "x-dead-letter-routing-key": "dead",
        },
    )
    await primary.bind(exchange, "send")
    for delay in (30, 60, 120, 240):
        queue = await channel.declare_queue(
            f"auth.email.retry.{delay}",
            durable=True,
            arguments={
                "x-message-ttl": delay * 1000,
                "x-dead-letter-exchange": EXCHANGE,
                "x-dead-letter-routing-key": "send",
            },
        )
        await queue.bind(exchange, f"retry.{delay}")
    return exchange


class EmailPublisher:
    def __init__(
        self, connection: aio_pika.abc.AbstractRobustConnection | None
    ) -> None:
        self._connection = connection

    async def publish(self, job: EmailJob) -> None:
        if self._connection is None:
            raise ServiceUnavailableError("Email delivery is temporarily unavailable.")
        channel = await self._connection.channel(publisher_confirms=True)
        try:
            exchange = await declare_topology(channel)
            await exchange.publish(
                aio_pika.Message(
                    job.to_bytes(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                ),
                routing_key="send",
            )
        finally:
            await channel.close()


def expired(job: EmailJob) -> bool:
    return job.expires_at <= datetime.now(UTC)
