from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Self, TypeVar

from alembic import command
from alembic.config import Config
from testcontainers.community.opensearch import OpenSearchContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.community.redis import RedisContainer

from src.application.environment.environment_manager import Settings


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_AUTH_JWT_SECRET = "test-secret-with-at-least-thirty-two-characters"


class ManagedContainer(Protocol):
    def start(self) -> Self: ...

    def stop(self, force: bool = True, delete_volume: bool = True) -> None: ...

    def get_container_host_ip(self) -> str: ...

    def get_exposed_port(self, port: int) -> int: ...


class ManagedPostgresContainer(ManagedContainer, Protocol):
    def get_connection_url(
        self, host: str | None = None, driver: str | None = None
    ) -> str: ...


@dataclass(frozen=True)
class ContainerFactories:
    postgres: Callable[[], ManagedPostgresContainer]
    redis: Callable[[], ManagedContainer]
    opensearch: Callable[[], ManagedContainer]
    rabbitmq: Callable[[], ManagedContainer]


def _postgres_container() -> ManagedPostgresContainer:
    return PostgresContainer(
        image="postgres:17-alpine",
        username="games",
        password="games",
        dbname="games",
        driver="asyncpg",
    )


def _redis_container() -> ManagedContainer:
    return RedisContainer(image="redis:7.4-alpine")


def _opensearch_container() -> ManagedContainer:
    return OpenSearchContainer(image="opensearchproject/opensearch:2.19.1").with_env(
        "OPENSEARCH_JAVA_OPTS", "-Xms512m -Xmx512m"
    )


def _rabbitmq_container() -> ManagedContainer:
    return RabbitMqContainer(
        image="rabbitmq:4.1-management",
        username="games",
        password="games",
    )


DEFAULT_CONTAINER_FACTORIES = ContainerFactories(
    postgres=_postgres_container,
    redis=_redis_container,
    opensearch=_opensearch_container,
    rabbitmq=_rabbitmq_container,
)


def apply_migrations(database_url: str) -> None:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


ContainerT = TypeVar("ContainerT", bound=ManagedContainer)


def _start_container(stack: ExitStack, container: ContainerT) -> ContainerT:
    # Register cleanup before startup because readiness checks can fail after Docker
    # has already created the container.
    stack.callback(container.stop)
    return container.start()


@contextmanager
def integration_container_stack(
    factories: ContainerFactories = DEFAULT_CONTAINER_FACTORIES,
    migrate: Callable[[str], None] = apply_migrations,
) -> Iterator[Settings]:
    with ExitStack() as stack:
        postgres = _start_container(stack, factories.postgres())
        redis = _start_container(stack, factories.redis())
        opensearch = _start_container(stack, factories.opensearch())
        rabbitmq = _start_container(stack, factories.rabbitmq())

        settings = Settings(
            database_url=postgres.get_connection_url(driver="asyncpg"),
            redis_url=(
                f"redis://{redis.get_container_host_ip()}:"
                f"{redis.get_exposed_port(6379)}/0"
            ),
            opensearch_url=(
                f"http://{opensearch.get_container_host_ip()}:"
                f"{opensearch.get_exposed_port(9200)}"
            ),
            rabbitmq_url=(
                f"amqp://games:games@{rabbitmq.get_container_host_ip()}:"
                f"{rabbitmq.get_exposed_port(5672)}/%2F"
            ),
            auth_jwt_secret=_AUTH_JWT_SECRET,
            auth_refresh_cookie_secure=False,
        )
        migrate(settings.database_url)
        yield settings
