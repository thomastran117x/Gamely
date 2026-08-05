from __future__ import annotations

import asyncio
import logging
from email.message import EmailMessage

import aio_pika
import aiosmtplib

from src.application.environment.environment_manager import Settings
from src.infrastructure.email.jobs import EmailJob, EXCHANGE, expired, declare_topology
from src.infrastructure.rabbitmq import connect_rabbitmq

logger = logging.getLogger(__name__)
RETRY_DELAYS = (30, 60, 120, 240)


def message_for(job: EmailJob, settings: Settings) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = job.recipient
    message["Subject"] = "Verify your Games account" if job.template == "verify" else "Reset your Games password"
    message.set_content(f"Your Games {job.template} code is: {job.code}\nThis code expires at {job.expires_at.isoformat()}.")
    return message


async def send(job: EmailJob, settings: Settings) -> None:
    if not settings.smtp_host or not settings.smtp_from:
        raise RuntimeError("SMTP is not configured")
    await aiosmtplib.send(message_for(job, settings), hostname=settings.smtp_host, port=settings.smtp_port, username=settings.smtp_username or None, password=settings.smtp_password or None, start_tls=settings.smtp_starttls)


async def main() -> None:
    settings = Settings()
    connection = await connect_rabbitmq(settings.rabbitmq_url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=settings.email_worker_concurrency)
    exchange = await declare_topology(channel)
    queue = await channel.get_queue("auth.email.primary")

    async def handle(message: aio_pika.abc.AbstractIncomingMessage) -> None:
        try:
            job = EmailJob.from_bytes(message.body)
            if expired(job):
                await message.ack()
                return
            await send(job, settings)
            await message.ack()
        except (ValueError, KeyError, TypeError):
            await message.reject(requeue=False)
        except Exception:
            try:
                job = EmailJob.from_bytes(message.body)
                if job.attempts >= len(RETRY_DELAYS):
                    await exchange.publish(aio_pika.Message(message.body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT), routing_key="dead")
                else:
                    retry = EmailJob(job.recipient, job.template, job.code, job.expires_at, job.attempts + 1, job.id)
                    await exchange.publish(aio_pika.Message(retry.to_bytes(), delivery_mode=aio_pika.DeliveryMode.PERSISTENT), routing_key=f"retry.{RETRY_DELAYS[job.attempts]}")
                await message.ack()
            except Exception:
                logger.exception("email_job_failed")
                await message.nack(requeue=True)

    await queue.consume(handle)
    try:
        await asyncio.Future()
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
