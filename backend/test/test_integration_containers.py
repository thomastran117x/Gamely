from __future__ import annotations

from dataclasses import dataclass, field
from typing import Self

import pytest

from test.integration_containers import (
    ContainerFactories,
    integration_container_stack,
)


@dataclass
class FakeContainer:
    name: str
    port: int
    events: list[str]
    fail_start: bool = False
    stopped: bool = field(default=False, init=False)

    def start(self) -> Self:
        self.events.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(f"{self.name} failed")
        return self

    def stop(self, force: bool = True, delete_volume: bool = True) -> None:
        del force, delete_volume
        self.stopped = True
        self.events.append(f"stop:{self.name}")

    def get_container_host_ip(self) -> str:
        return "127.0.0.1"

    def get_exposed_port(self, port: int) -> int:
        assert port == self.port
        return self.port + 10_000

    def get_connection_url(
        self, host: str | None = None, driver: str | None = None
    ) -> str:
        del host
        assert driver == "asyncpg"
        return "postgresql+asyncpg://games:games@127.0.0.1:15432/games"


def build_factories(
    events: list[str], failing_service: str | None = None
) -> tuple[ContainerFactories, list[FakeContainer]]:
    containers = [
        FakeContainer("postgres", 5432, events, failing_service == "postgres"),
        FakeContainer("redis", 6379, events, failing_service == "redis"),
        FakeContainer("opensearch", 9200, events, failing_service == "opensearch"),
        FakeContainer("rabbitmq", 5672, events, failing_service == "rabbitmq"),
    ]
    return (
        ContainerFactories(
            postgres=lambda: containers[0],
            redis=lambda: containers[1],
            opensearch=lambda: containers[2],
            rabbitmq=lambda: containers[3],
        ),
        containers,
    )


def test_stack_starts_all_services_migrates_once_and_stops_in_reverse() -> None:
    events: list[str] = []
    factories, containers = build_factories(events)
    migration_urls: list[str] = []

    with integration_container_stack(factories, migration_urls.append) as settings:
        assert settings.database_url.endswith("/games")
        assert settings.redis_url == "redis://127.0.0.1:16379/0"
        assert len(migration_urls) == 1

    assert all(container.stopped for container in containers)
    assert events == [
        "start:postgres",
        "start:redis",
        "start:opensearch",
        "start:rabbitmq",
        "stop:rabbitmq",
        "stop:opensearch",
        "stop:redis",
        "stop:postgres",
    ]


def test_stack_stops_every_service_when_test_body_fails() -> None:
    events: list[str] = []
    factories, containers = build_factories(events)

    with pytest.raises(RuntimeError, match="test failed"):
        with integration_container_stack(factories, lambda _url: None):
            raise RuntimeError("test failed")

    assert all(container.stopped for container in containers)


@pytest.mark.parametrize(
    "failing_service", ["postgres", "redis", "opensearch", "rabbitmq"]
)
def test_stack_cleans_up_partial_startup(failing_service: str) -> None:
    events: list[str] = []
    factories, containers = build_factories(events, failing_service)

    with pytest.raises(RuntimeError, match=f"{failing_service} failed"):
        with integration_container_stack(factories, lambda _url: None):
            pytest.fail("a failed stack must not yield")

    started_count = next(
        index + 1
        for index, container in enumerate(containers)
        if container.name == failing_service
    )
    assert all(container.stopped for container in containers[:started_count])
    assert not any(container.stopped for container in containers[started_count:])


def test_stack_cleans_up_when_migration_fails() -> None:
    events: list[str] = []
    factories, containers = build_factories(events)

    def fail_migration(_database_url: str) -> None:
        raise RuntimeError("migration failed")

    with pytest.raises(RuntimeError, match="migration failed"):
        with integration_container_stack(factories, fail_migration):
            pytest.fail("a failed migration must not yield")

    assert all(container.stopped for container in containers)
