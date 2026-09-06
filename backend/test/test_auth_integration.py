from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy import text

from src.application.environment.environment_manager import Settings
from src.features.auth.availability.email_filter import (
    EMAIL_FILTER_KEY,
    EMAIL_FILTER_META_KEY,
)
from src.main import create_app


@pytest.mark.integration
def test_signup_login_and_refresh_against_containers(
    integration_settings: Settings,
) -> None:
    email = f"auth-integration-{uuid4().hex}@example.com"
    with TestClient(create_app(integration_settings)) as client:
        signup = client.post(
            "/auth/signup", json={"email": email, "password": "a-secure-password"}
        )
        assert signup.status_code == 202

        login = client.post(
            "/auth/login", json={"email": email, "password": "a-secure-password"}
        )
        assert login.status_code == 200
        assert login.json()["token_type"] == "bearer"
        assert integration_settings.auth_refresh_cookie_name in login.cookies

        refreshed = client.post("/auth/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"]


@pytest.mark.integration
def test_auth_security_and_revocation_flows_against_containers(
    integration_settings: Settings,
) -> None:
    email = f"auth-security-{uuid4().hex}@example.com"
    initial_password = "initial-password"
    replacement_password = "replacement-password"
    cookie_name = integration_settings.auth_refresh_cookie_name

    with TestClient(create_app(integration_settings)) as client:
        assert (
            client.post(
                "/auth/signup", json={"email": email, "password": initial_password}
            ).status_code
            == 202
        )

        duplicate = client.post(
            "/auth/signup", json={"email": email, "password": initial_password}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "conflict"

        wrong_login = client.post(
            "/auth/login", json={"email": email, "password": "incorrect-password"}
        )
        assert wrong_login.status_code == 401
        assert wrong_login.json()["error"]["code"] == "unauthorized"

        first_login = client.post(
            "/auth/login", json={"email": email, "password": initial_password}
        )
        first_access = first_login.json()["access_token"]
        first_refresh = first_login.cookies[cookie_name]

        assert client.get("/auth/me").status_code == 401
        assert (
            client.get(
                "/auth/me", headers={"Authorization": "Bearer invalid-token"}
            ).status_code
            == 401
        )
        profile = client.get(
            "/auth/me", headers={"Authorization": f"Bearer {first_access}"}
        )
        assert profile.status_code == 200
        assert profile.json()["email"] == email
        assert profile.json()["email_verified"] is False

        wrong_change = client.post(
            "/auth/change-password",
            headers={"Authorization": f"Bearer {first_access}"},
            json={
                "current_password": "incorrect-password",
                "new_password": replacement_password,
            },
        )
        assert wrong_change.status_code == 401

        second_login = client.post(
            "/auth/login", json={"email": email, "password": initial_password}
        )
        second_refresh = second_login.cookies[cookie_name]
        changed = client.post(
            "/auth/change-password",
            headers={"Authorization": f"Bearer {first_access}"},
            json={
                "current_password": initial_password,
                "new_password": replacement_password,
            },
        )
        assert changed.status_code == 200
        assert cookie_name not in client.cookies

        assert (
            client.post(
                "/auth/login", json={"email": email, "password": initial_password}
            ).status_code
            == 401
        )
        replacement_login = client.post(
            "/auth/login", json={"email": email, "password": replacement_password}
        )
        assert replacement_login.status_code == 200

        for revoked_refresh in (first_refresh, second_refresh):
            client.cookies.clear()
            client.cookies.set(
                cookie_name,
                revoked_refresh,
                path=integration_settings.auth_refresh_cookie_path,
            )
            assert client.post("/auth/refresh").status_code == 401

        assert (
            client.post(
                "/auth/forgot-password", json={"email": "unknown@example.com"}
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/auth/resend-verification", json={"email": "unknown@example.com"}
            ).status_code
            == 202
        )

        client.cookies.clear()
        current_login = client.post(
            "/auth/login", json={"email": email, "password": replacement_password}
        )
        current_refresh = current_login.cookies[cookie_name]
        assert client.post("/auth/logout").status_code == 204
        client.cookies.set(
            cookie_name,
            current_refresh,
            path=integration_settings.auth_refresh_cookie_path,
        )
        assert client.post("/auth/refresh").status_code == 401


def filter_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


@pytest.mark.integration
async def test_signup_populates_the_email_filter_against_containers(
    integration_settings: Settings,
) -> None:
    email = f"auth-filter-{uuid4().hex}@example.com"
    redis = filter_client(integration_settings)
    try:
        with TestClient(create_app(integration_settings)) as client:
            assert (
                client.post(
                    "/auth/signup",
                    json={"email": email, "password": "a-secure-password"},
                ).status_code
                == 202
            )
        assert await redis.cf().exists(EMAIL_FILTER_KEY, email) == 1
    finally:
        await redis.aclose()


@pytest.mark.integration
def test_email_availability_endpoint_against_containers(
    integration_settings: Settings,
) -> None:
    email = f"auth-available-{uuid4().hex}@example.com"
    with TestClient(create_app(integration_settings)) as client:
        before = client.post("/auth/email/availability", json={"email": email})
        assert before.status_code == 200
        assert before.json() == {"available": True}
        assert before.headers["Cache-Control"] == "no-store"

        client.post(
            "/auth/signup", json={"email": email, "password": "a-secure-password"}
        )

        after = client.post("/auth/email/availability", json={"email": email})
        assert after.json() == {"available": False}


@pytest.mark.integration
def test_email_availability_endpoint_throttles_repeated_requests(
    integration_settings: Settings,
) -> None:
    settings = integration_settings.model_copy(
        update={"auth_availability_limit": 3, "auth_jwt_secret": uuid4().hex * 2}
    )
    email = f"auth-throttle-{uuid4().hex}@example.com"
    with TestClient(create_app(settings)) as client:
        codes = [
            client.post("/auth/email/availability", json={"email": email}).status_code
            for _ in range(4)
        ]

    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


@pytest.mark.integration
async def test_startup_rebuilds_the_filter_after_redis_is_flushed(
    integration_settings: Settings,
) -> None:
    email = f"auth-rebuild-{uuid4().hex}@example.com"
    redis = filter_client(integration_settings)
    try:
        with TestClient(create_app(integration_settings)) as client:
            client.post(
                "/auth/signup", json={"email": email, "password": "a-secure-password"}
            )
        await redis.flushall()

        # A fresh lifespan runs the warmer, which rebuilds from the database.
        with TestClient(create_app(integration_settings)) as client:
            assert await redis.cf().exists(EMAIL_FILTER_KEY, email) == 1
            assert await redis.get(EMAIL_FILTER_META_KEY) is not None

            duplicate = client.post(
                "/auth/signup", json={"email": email, "password": "a-secure-password"}
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["error"]["code"] == "conflict"
    finally:
        await redis.aclose()


@pytest.mark.integration
def test_signup_still_succeeds_when_the_filter_is_disabled(
    integration_settings: Settings,
) -> None:
    settings = integration_settings.model_copy(
        update={"auth_email_filter_enabled": False}
    )
    email = f"auth-nofilter-{uuid4().hex}@example.com"
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/auth/signup", json={"email": email, "password": "a-secure-password"}
            ).status_code
            == 202
        )
        duplicate = client.post(
            "/auth/signup", json={"email": email, "password": "a-secure-password"}
        )
        assert duplicate.status_code == 409
