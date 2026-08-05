from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from inspect import isawaitable
from threading import RLock
from typing import Any, Generic, Protocol, TypeVar, cast

T = TypeVar("T")


class ServiceLifetime(str, Enum):
    SINGLETON = "singleton"
    SCOPED = "scoped"
    TRANSIENT = "transient"


class ServiceNotRegisteredError(LookupError):
    pass


class ServiceResolver(Protocol):
    def get(self, service_type: type[T]) -> T: ...


ServiceFactory = Callable[[ServiceResolver], T]


@dataclass(frozen=True)
class ServiceDescriptor(Generic[T]):
    service_type: type[T]
    factory: ServiceFactory[T]
    lifetime: ServiceLifetime


class ServiceCollection:
    """Registers application services before building an immutable provider."""

    def __init__(self) -> None:
        self._descriptors: dict[type[Any], ServiceDescriptor[Any]] = {}

    def add_singleton(self, service_type: type[T], factory: ServiceFactory[T]) -> None:
        self._add(ServiceDescriptor(service_type, factory, ServiceLifetime.SINGLETON))

    def add_scoped(self, service_type: type[T], factory: ServiceFactory[T]) -> None:
        self._add(ServiceDescriptor(service_type, factory, ServiceLifetime.SCOPED))

    def add_transient(self, service_type: type[T], factory: ServiceFactory[T]) -> None:
        self._add(ServiceDescriptor(service_type, factory, ServiceLifetime.TRANSIENT))

    def add_instance(self, service_type: type[T], instance: T) -> None:
        self.add_singleton(service_type, lambda _resolver: instance)

    def build_provider(self) -> ServiceProvider:
        return ServiceProvider(self._descriptors.copy())

    def _add(self, descriptor: ServiceDescriptor[T]) -> None:
        if descriptor.service_type in self._descriptors:
            raise ValueError(
                f"Service already registered: {descriptor.service_type.__name__}"
            )
        self._descriptors[descriptor.service_type] = descriptor


class ServiceProvider(ServiceResolver):
    """Root provider that owns singleton instances and creates request scopes."""

    def __init__(self, descriptors: dict[type[Any], ServiceDescriptor[Any]]) -> None:
        self._descriptors = descriptors
        self._singletons: dict[type[Any], Any] = {}
        self._lock = RLock()

    def get(self, service_type: type[T]) -> T:
        descriptor = self._get_descriptor(service_type)
        if descriptor.lifetime is ServiceLifetime.SCOPED:
            raise RuntimeError(
                f"Scoped service requires a scope: {service_type.__name__}"
            )
        return cast(T, self._resolve(descriptor, self, self._singletons))

    def create_scope(self) -> ServiceScope:
        return ServiceScope(self)

    def _get_descriptor(self, service_type: type[T]) -> ServiceDescriptor[Any]:
        try:
            return self._descriptors[service_type]
        except KeyError as exc:
            raise ServiceNotRegisteredError(service_type.__name__) from exc

    def _resolve(
        self,
        descriptor: ServiceDescriptor[Any],
        resolver: ServiceResolver,
        cache: dict[type[Any], Any],
    ) -> Any:
        if descriptor.lifetime is ServiceLifetime.TRANSIENT:
            return descriptor.factory(resolver)
        with self._lock:
            if descriptor.service_type not in cache:
                cache[descriptor.service_type] = descriptor.factory(resolver)
            return cache[descriptor.service_type]


class ServiceScope(ServiceResolver):
    """Request-scoped provider that owns scoped instances and async cleanup."""

    def __init__(self, root: ServiceProvider) -> None:
        self._root = root
        self._scoped: dict[type[Any], Any] = {}
        self._closed = False

    def get(self, service_type: type[T]) -> T:
        if self._closed:
            raise RuntimeError("Service scope is closed")
        descriptor = self._root._get_descriptor(service_type)
        if descriptor.lifetime is ServiceLifetime.SCOPED:
            return cast(T, self._root._resolve(descriptor, self, self._scoped))
        return self._root.get(service_type)

    async def __aenter__(self) -> ServiceScope:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for instance in reversed(list(self._scoped.values())):
            await _dispose(instance)
        self._scoped.clear()


async def _dispose(instance: object) -> None:
    for method_name in ("aclose", "close"):
        method = getattr(instance, method_name, None)
        if callable(method):
            result = method()
            if isawaitable(result):
                await cast(Awaitable[object], result)
            return
