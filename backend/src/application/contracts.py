class ApplicationServices:
    """Lifecycle contract for infrastructure owned by the application container."""

    async def connect(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def ready(self) -> None:
        raise NotImplementedError
