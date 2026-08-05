import aio_pika


async def connect_rabbitmq(url: str) -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(url)
