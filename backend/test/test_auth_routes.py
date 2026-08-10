from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.application.environment.environment_manager import Settings
from src.features.auth.auth_controller import AuthController, get_controller, router
from src.features.auth.auth_model import User
from src.features.auth.auth_service import AuthService
from src.features.auth.oauth.oauth_service import OAuthService
from src.features.auth.token.token_service import TokenService
from src.shared.exceptions import UnauthorizedError
from src.shared.middlewares import (
    ExceptionHandlingMiddleware,
    register_http_exception_handlers,
)


class FakeAuthService:
    def __init__(self) -> None:
        self.user = cast(
            User,
            SimpleNamespace(
                id=uuid4(),
                email="user@example.com",
                email_verified_at=datetime.now(UTC),
            ),
        )
        self.login_error: Exception | None = None
        self.changed_passwords: list[tuple[UUID, str, str]] = []

    async def signup(self, _: str, __: str) -> None:
        return None

    async def login(self, _: str, __: str) -> tuple[User, str, str]:
        if self.login_error:
            raise self.login_error
        return self.user, "access-token", "refresh-token"

    async def verify_email(self, _: str, __: str) -> None:
        return None

    async def send_verification(self, _: str) -> None:
        return None

    async def forgot_password(self, _: str) -> None:
        return None

    async def reset_password(self, _: str, __: str, ___: str) -> None:
        return None

    async def change_password(self, user_id: UUID, current: str, new: str) -> None:
        self.changed_passwords.append((user_id, current, new))

    async def refresh(self, _: str) -> tuple[User, str, str]:
        return self.user, "new-access-token", "new-refresh-token"

    async def _require_user(self, _: UUID) -> User:
        return self.user


class FakeTokenService:
    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id
        self.revoked_refresh: list[str] = []
        self.revoked_users: list[UUID] = []

    def decode_access(self, value: str) -> UUID:
        if value != "valid-access":
            raise UnauthorizedError("The access token is invalid or expired.")
        return self.user_id

    async def revoke_refresh(self, value: str) -> None:
        self.revoked_refresh.append(value)

    async def revoke_all(self, user_id: UUID) -> None:
        self.revoked_users.append(user_id)


class FakeOAuthService:
    async def create_challenge(self, _: str) -> str:
        return "nonce"

    async def login(self, _: str, __: str, ___: str) -> tuple[User, str, str]:
        raise AssertionError("OAuth login was not expected")


@pytest.fixture
def auth_routes() -> Generator[
    tuple[TestClient, FakeAuthService, FakeTokenService, Settings]
]:
    settings = Settings(
        auth_jwt_secret="test-secret-with-at-least-thirty-two-characters",
        auth_refresh_cookie_secure=True,
    )
    auth = FakeAuthService()
    tokens = FakeTokenService(auth.user.id)
    controller = AuthController(
        cast(AuthService, auth),
        cast(OAuthService, FakeOAuthService()),
        cast(TokenService, tokens),
        settings,
    )
    app = FastAPI()
    app.include_router(router)
    register_http_exception_handlers(app)
    app.add_middleware(ExceptionHandlingMiddleware)
    app.dependency_overrides[get_controller] = lambda: controller
    with TestClient(app) as client:
        yield client, auth, tokens, settings


def test_login_sets_hardened_refresh_cookie(
    auth_routes: tuple[TestClient, FakeAuthService, FakeTokenService, Settings],
) -> None:
    client, _, _, settings = auth_routes

    response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "a-secure-password"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "access-token",
        "token_type": "bearer",
    }
    cookie = response.headers["set-cookie"]
    assert f"{settings.auth_refresh_cookie_name}=refresh-token" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/auth" in cookie
    assert "Max-Age=2592000" in cookie


def test_login_failure_uses_safe_error_contract(
    auth_routes: tuple[TestClient, FakeAuthService, FakeTokenService, Settings],
) -> None:
    client, auth, _, _ = auth_routes
    auth.login_error = UnauthorizedError("The email address or password is incorrect.")

    response = client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "unauthorized",
            "message": "The email address or password is incorrect.",
        }
    }


def test_refresh_requires_cookie(
    auth_routes: tuple[TestClient, FakeAuthService, FakeTokenService, Settings],
) -> None:
    client, _, _, _ = auth_routes

    response = client.post("/auth/refresh")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    ("method", "path", "json"),
    [
        ("get", "/auth/me", None),
        (
            "post",
            "/auth/change-password",
            {"current_password": "old-password", "new_password": "new-password"},
        ),
        ("post", "/auth/logout-all", None),
    ],
)
def test_protected_routes_require_bearer_token(
    auth_routes: tuple[TestClient, FakeAuthService, FakeTokenService, Settings],
    method: str,
    path: str,
    json: dict[str, str] | None,
) -> None:
    client, _, _, _ = auth_routes

    response = client.request(method, path, json=json)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/auth/signup",
            {"email": "user@example.com", "password": "x" * 129},
        ),
        (
            "/auth/verify-email",
            {"email": "user@example.com", "code": "12345"},
        ),
        (
            "/auth/oauth/google",
            {"id_token": "x" * 16385, "nonce": "nonce"},
        ),
    ],
)
def test_auth_request_bounds_return_generic_validation_error(
    auth_routes: tuple[TestClient, FakeAuthService, FakeTokenService, Settings],
    path: str,
    body: dict[str, str],
) -> None:
    client, _, _, _ = auth_routes

    response = client.post(path, json=body)

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "The request data is invalid.",
        }
    }


def test_logout_revokes_current_family_and_clears_cookie(
    auth_routes: tuple[TestClient, FakeAuthService, FakeTokenService, Settings],
) -> None:
    client, _, tokens, settings = auth_routes
    client.cookies.set(
        settings.auth_refresh_cookie_name,
        "refresh-token",
        path=settings.auth_refresh_cookie_path,
    )

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert response.content == b""
    assert tokens.revoked_refresh == ["refresh-token"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_change_password_revokes_cookie_after_authentication(
    auth_routes: tuple[TestClient, FakeAuthService, FakeTokenService, Settings],
) -> None:
    client, auth, _, _ = auth_routes

    response = client.post(
        "/auth/change-password",
        headers={"Authorization": "Bearer valid-access"},
        json={
            "current_password": "current-password",
            "new_password": "replacement-password",
        },
    )

    assert response.status_code == 200
    assert auth.changed_passwords == [
        (auth.user.id, "current-password", "replacement-password")
    ]
    assert "Max-Age=0" in response.headers["set-cookie"]
