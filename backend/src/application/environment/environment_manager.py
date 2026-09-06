from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_ROOT_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )
    app_name: str = "Games API"
    database_url: str = "postgresql+asyncpg://games:games@127.0.0.1:5432/games"
    redis_url: str = "redis://127.0.0.1:6379/0"
    opensearch_url: str = "http://127.0.0.1:9200"
    rabbitmq_url: str = "amqp://games:games@127.0.0.1:5672/"
    aws_region: str = "ca-central-1"
    s3_bucket: str | None = None
    cors_origins: str = "http://localhost:3040,http://127.0.0.1:3040"
    auth_jwt_secret: str
    auth_jwt_issuer: str = "games-api"
    auth_jwt_audience: str = "games-api"
    auth_access_token_minutes: int = 15
    auth_refresh_token_days: int = 30
    auth_code_minutes: int = 10
    auth_refresh_cookie_name: str = "games_refresh"
    auth_refresh_cookie_domain: str | None = None
    auth_refresh_cookie_path: str = "/auth"
    auth_refresh_cookie_secure: bool = True
    auth_refresh_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_email_filter_enabled: bool = True
    auth_email_filter_capacity: int = Field(default=100_000, ge=1)
    auth_email_filter_bucket_size: int = Field(default=4, ge=1, le=255)
    auth_email_filter_expansion: int = Field(default=2, ge=1)
    auth_email_filter_max_iterations: int = Field(default=20, ge=1)
    auth_email_filter_rebuild_batch_size: int = Field(default=1000, ge=1)
    auth_email_filter_rebuild_timeout_seconds: int = Field(default=30, ge=1)
    auth_email_filter_lock_seconds: int = Field(default=300, ge=1)
    auth_availability_limit: int = Field(default=20, ge=1)
    auth_signup_limit: int = Field(default=10, ge=1)
    auth_throttle_window_seconds: int = Field(default=60, ge=1)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    email_worker_concurrency: int = 10
    google_client_id: str = ""
    microsoft_client_id: str = ""
    microsoft_tenant_id: str = ""
    apple_client_id: str = ""

    @field_validator("database_url", "redis_url", "opensearch_url", "rabbitmq_url")
    @classmethod
    def connection_url_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("connection URL must not be blank")
        return value

    @field_validator("auth_jwt_secret")
    @classmethod
    def jwt_secret_must_be_strong(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("AUTH_JWT_SECRET must be at least 32 characters")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]
