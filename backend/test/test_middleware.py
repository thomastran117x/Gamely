from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.application.environment.environment_manager import Settings
from src.main import create_app
from src.shared.exceptions import NotFoundError
from test.conftest import FakeServices


def create_test_app() -> FastAPI:
    app = create_app(Settings(auth_jwt_secret="test-secret-with-at-least-thirty-two-characters"), services_factory=FakeServices)

    @app.get("/custom-error")
    async def custom_error() -> None:
        raise NotFoundError("Game not found.")

    @app.get("/http-error")
    async def http_error() -> None:
        raise HTTPException(status_code=400, detail="The supplied value is invalid.")

    @app.get("/unexpected-error")
    async def unexpected_error() -> None:
        raise RuntimeError("internal implementation detail")

    return app


def test_exception_middleware_maps_application_errors() -> None:
    with TestClient(create_test_app(), raise_server_exceptions=False) as client:
        response = client.get("/custom-error")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Game not found."}
    }


def test_exception_middleware_hides_unknown_error_details() -> None:
    with TestClient(create_test_app(), raise_server_exceptions=False) as client:
        response = client.get("/unexpected-error")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    assert "implementation detail" not in response.text


def test_http_error_and_response_middleware() -> None:
    with TestClient(create_test_app()) as client:
        response = client.get("/http-error", headers={"X-Request-ID": "request-123"})
        preflight = client.options(
            "/",
            headers={
                "Origin": "http://localhost:3040",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "http_error", "message": "The supplied value is invalid."}
    }
    assert response.headers["X-Request-ID"] == "request-123"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3040"