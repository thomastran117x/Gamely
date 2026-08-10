import os
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "AUTH_JWT_SECRET", "test-secret-with-at-least-thirty-two-characters"
)

from src.application.contracts import ApplicationServices
from src.application.environment.environment_manager import Settings
from src.main import create_app
from test.integration_containers import integration_container_stack


class FakeServices(ApplicationServices):
    def __init__(
        self, _settings: Settings, ready_error: Exception | None = None
    ) -> None:
        self.ready_error = ready_error
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def ready(self) -> None:
        if self.ready_error:
            raise self.ready_error


@pytest.fixture
def settings() -> Settings:
    return Settings(auth_jwt_secret="test-secret-with-at-least-thirty-two-characters")


@pytest.fixture
def client(settings: Settings) -> Generator[TestClient]:
    app = create_app(settings=settings, services_factory=FakeServices)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def integration_settings() -> Generator[Settings]:
    with integration_container_stack() as container_settings:
        yield container_settings
