from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import cast

import aio_pika
import aiosmtplib
import pytest

from src.application.environment.environment_manager import Settings
from src.infrastructure.email import EmailJob
from src.infrastructure.email.jobs import expired
from src.workers import email_worker


class FakeMessage:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.acked = 0
        self.rejections: list[bool] = []

    async def ack(self) -> None:
        self.acked += 1

    async def reject(self, requeue: bool = False) -> None:
        self.rejections.append(requeue)


class FakeExchange:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.published: list[tuple[aio_pika.Message, str]] = []

    async def publish(self, message: aio_pika.Message, routing_key: str) -> None:
        if self.error:
            raise self.error
        self.published.append((message, routing_key))


def settings() -> Settings:
    return Settings(
        auth_jwt_secret="test-secret-with-at-least-thirty-two-characters",
        smtp_host="smtp.example.com",
        smtp_from="noreply@example.com",
    )


def job(*, attempts: int = 0, expired_at: bool = False) -> EmailJob:
    offset = -1 if expired_at else 10
    return EmailJob(
        "user@example.com",
        "verify",
        "123456",
        datetime.now(UTC) + timedelta(minutes=offset),
        attempts,
        "job-id",
    )


def test_email_job_round_trip_generates_id_and_preserves_fields() -> None:
    original = EmailJob(
        "user@example.com",
        "reset",
        "654321",
        datetime.now(UTC) + timedelta(minutes=10),
    )

    decoded = EmailJob.from_bytes(original.to_bytes())

    assert decoded.recipient == original.recipient
    assert decoded.template == "reset"
    assert decoded.code == "654321"
    assert decoded.expires_at == original.expires_at
    assert decoded.attempts == 0
    assert decoded.id


@pytest.mark.parametrize(
    "overrides",
    [
        {"recipient": "not-an-email"},
        {"template": "unknown"},
        {"code": "12345"},
        {"expires_at": datetime.now()},
        {"attempts": -1},
    ],
)
def test_email_job_rejects_invalid_contract_fields(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "recipient": "user@example.com",
        "template": "verify",
        "code": "123456",
        "expires_at": datetime.now(UTC) + timedelta(minutes=10),
        "attempts": 0,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        EmailJob(**values)  # type: ignore[arg-type]


def test_expired_and_message_rendering() -> None:
    expired_job = job(expired_at=True)

    assert expired(expired_job) is True
    message = email_worker.message_for(expired_job, settings())
    assert message["From"] == "noreply@example.com"
    assert message["To"] == "user@example.com"
    assert message["Subject"] == "Verify your Games account"
    assert "123456" in message.get_content()


@pytest.mark.asyncio
async def test_expired_job_is_acknowledged_without_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = FakeMessage(job(expired_at=True).to_bytes())
    exchange = FakeExchange()

    async def unexpected_send(_: EmailJob, __: Settings) -> None:
        raise AssertionError("expired email must not be sent")

    monkeypatch.setattr(email_worker, "send", unexpected_send)
    await email_worker.handle_message(
        cast(aio_pika.abc.AbstractIncomingMessage, message),
        cast(aio_pika.abc.AbstractExchange, exchange),
        settings(),
    )

    assert message.acked == 1
    assert message.rejections == []
    assert exchange.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        json.dumps(
            {
                "recipient": "user@example.com",
                "template": "unknown",
                "code": "123456",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            }
        ).encode(),
        json.dumps(
            {
                "recipient": "user@example.com",
                "template": "verify",
                "code": "123456",
                "expires_at": datetime.now().isoformat(),
            }
        ).encode(),
    ],
)
async def test_malformed_job_is_rejected_without_requeue(body: bytes) -> None:
    message = FakeMessage(body)
    exchange = FakeExchange()

    await email_worker.handle_message(
        cast(aio_pika.abc.AbstractIncomingMessage, message),
        cast(aio_pika.abc.AbstractExchange, exchange),
        settings(),
    )

    assert message.acked == 0
    assert message.rejections == [False]
    assert exchange.published == []


@pytest.mark.asyncio
async def test_successful_delivery_is_acknowledged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = FakeMessage(job().to_bytes())
    exchange = FakeExchange()
    sent: list[EmailJob] = []

    async def successful_send(delivery: EmailJob, _: Settings) -> None:
        sent.append(delivery)

    monkeypatch.setattr(email_worker, "send", successful_send)
    await email_worker.handle_message(
        cast(aio_pika.abc.AbstractIncomingMessage, message),
        cast(aio_pika.abc.AbstractExchange, exchange),
        settings(),
    )

    assert [delivery.id for delivery in sent] == ["job-id"]
    assert message.acked == 1
    assert message.rejections == []


@pytest.mark.asyncio
async def test_transient_failure_routes_incremented_job_to_retry_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = FakeMessage(job(attempts=0).to_bytes())
    exchange = FakeExchange()

    async def failing_send(_: EmailJob, __: Settings) -> None:
        raise ConnectionError("SMTP unavailable")

    monkeypatch.setattr(email_worker, "send", failing_send)
    await email_worker.handle_message(
        cast(aio_pika.abc.AbstractIncomingMessage, message),
        cast(aio_pika.abc.AbstractExchange, exchange),
        settings(),
    )

    assert message.acked == 1
    assert message.rejections == []
    published, routing_key = exchange.published[0]
    assert routing_key == "retry.30"
    retry = EmailJob.from_bytes(published.body)
    assert retry.attempts == 1
    assert retry.id == "job-id"


@pytest.mark.asyncio
async def test_retry_exhaustion_routes_original_job_to_dead_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = job(attempts=len(email_worker.RETRY_DELAYS))
    message = FakeMessage(delivery.to_bytes())
    exchange = FakeExchange()

    async def failing_send(_: EmailJob, __: Settings) -> None:
        raise TimeoutError

    monkeypatch.setattr(email_worker, "send", failing_send)
    await email_worker.handle_message(
        cast(aio_pika.abc.AbstractIncomingMessage, message),
        cast(aio_pika.abc.AbstractExchange, exchange),
        settings(),
    )

    assert message.acked == 1
    published, routing_key = exchange.published[0]
    assert routing_key == "dead"
    assert EmailJob.from_bytes(published.body) == delivery


@pytest.mark.asyncio
async def test_permanent_recipient_failure_is_rejected_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = FakeMessage(job().to_bytes())
    exchange = FakeExchange()

    async def refused_send(_: EmailJob, __: Settings) -> None:
        raise aiosmtplib.SMTPRecipientRefused(
            550, "unknown recipient", "bad@example.com"
        )

    monkeypatch.setattr(email_worker, "send", refused_send)
    await email_worker.handle_message(
        cast(aio_pika.abc.AbstractIncomingMessage, message),
        cast(aio_pika.abc.AbstractExchange, exchange),
        settings(),
    )

    assert message.acked == 0
    assert message.rejections == [False]
    assert exchange.published == []


@pytest.mark.asyncio
async def test_republish_failure_rejects_without_tight_requeue_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = FakeMessage(job().to_bytes())
    exchange = FakeExchange(ConnectionError("RabbitMQ unavailable"))

    async def failing_send(_: EmailJob, __: Settings) -> None:
        raise ConnectionError("SMTP unavailable")

    monkeypatch.setattr(email_worker, "send", failing_send)
    await email_worker.handle_message(
        cast(aio_pika.abc.AbstractIncomingMessage, message),
        cast(aio_pika.abc.AbstractExchange, exchange),
        settings(),
    )

    assert message.acked == 0
    assert message.rejections == [False]
