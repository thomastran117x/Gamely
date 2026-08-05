from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.application.environment.environment_manager import Settings
from src.main import create_app


@pytest.mark.integration
def test_signup_login_and_refresh_against_compose() -> None:
    email = f"auth-integration-{uuid4().hex}@example.com"
    settings = Settings(auth_jwt_secret="test-secret-with-at-least-thirty-two-characters", auth_refresh_cookie_secure=False)
    with TestClient(create_app(settings)) as client:
        signup = client.post("/auth/signup", json={"email": email, "password": "a-secure-password"})
        assert signup.status_code == 202

        login = client.post("/auth/login", json={"email": email, "password": "a-secure-password"})
        assert login.status_code == 200
        assert login.json()["token_type"] == "bearer"
        assert settings.auth_refresh_cookie_name in login.cookies

        refreshed = client.post("/auth/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"]