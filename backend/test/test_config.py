import pytest
from pydantic import ValidationError

from src.application.environment.environment_manager import Settings


def test_settings_reads_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-with-at-least-thirty-two-characters")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db:5432/test")
    monkeypatch.setenv("S3_BUCKET", "games-uploads")

    settings = Settings()  # type: ignore[call-arg]

    assert settings.database_url.endswith("/test")
    assert settings.s3_bucket == "games-uploads"


def test_settings_rejects_non_string_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-with-at-least-thirty-two-characters")
    monkeypatch.setenv("DATABASE_URL", "")

    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]


def test_settings_rejects_missing_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "")
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]