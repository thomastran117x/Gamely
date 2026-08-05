from fastapi.testclient import TestClient

from src.application.environment.environment_manager import Settings
from src.main import create_app
from test.conftest import FakeServices


def test_root_returns_api_metadata(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"name": "Games API", "status": "ok"}


def test_liveness_returns_ok(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_returns_ok(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_service_unavailable(settings: Settings) -> None:
    def unavailable_services(config: Settings) -> FakeServices:
        return FakeServices(config, ready_error=ConnectionError("Redis unavailable"))

    app = create_app(settings=settings, services_factory=unavailable_services)
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
