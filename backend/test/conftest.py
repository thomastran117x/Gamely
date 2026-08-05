from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from src.application.contracts import ApplicationServices
from src.application.environment.environment_manager import Settings
from src.main import create_app


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
    return Settings()


@pytest.fixture
def client(settings: Settings) -> Generator[TestClient]:
    app = create_app(settings=settings, services_factory=FakeServices)
    with TestClient(app) as test_client:
        yield test_client
