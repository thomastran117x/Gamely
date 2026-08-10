import pytest

from src.application.ioc import ServiceCollection


class SingletonService:
    pass


class ScopedService:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class TransientService:
    pass


def test_ioc_honours_singleton_and_transient_lifetimes() -> None:
    services = ServiceCollection()
    services.add_singleton(SingletonService, lambda _resolver: SingletonService())
    services.add_transient(TransientService, lambda _resolver: TransientService())
    provider = services.build_provider()

    assert provider.get(SingletonService) is provider.get(SingletonService)
    assert provider.get(TransientService) is not provider.get(TransientService)


@pytest.mark.asyncio
async def test_ioc_honours_scoped_lifetime_and_disposes_instances() -> None:
    services = ServiceCollection()
    services.add_scoped(ScopedService, lambda _resolver: ScopedService())
    provider = services.build_provider()

    async with provider.create_scope() as first_scope:
        first = first_scope.get(ScopedService)
        assert first is first_scope.get(ScopedService)

    async with provider.create_scope() as second_scope:
        assert first is not second_scope.get(ScopedService)

    assert first.closed is True


class ScopedConsumer:
    def __init__(self, dependency: ScopedService) -> None:
        self.dependency = dependency


@pytest.mark.asyncio
async def test_scoped_service_can_depend_on_another_scoped_service() -> None:
    services = ServiceCollection()
    services.add_scoped(ScopedService, lambda _resolver: ScopedService())
    services.add_scoped(
        ScopedConsumer, lambda resolver: ScopedConsumer(resolver.get(ScopedService))
    )
    async with services.build_provider().create_scope() as scope:
        consumer = scope.get(ScopedConsumer)
        assert consumer.dependency is scope.get(ScopedService)
