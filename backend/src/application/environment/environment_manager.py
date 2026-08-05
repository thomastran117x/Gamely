from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_name: str = "Games API"
    database_url: str = "postgresql+asyncpg://games:games@127.0.0.1:5432/games"
    redis_url: str = "redis://127.0.0.1:6379/0"
    opensearch_url: str = "http://127.0.0.1:9200"
    rabbitmq_url: str = "amqp://games:games@127.0.0.1:5672/"
    aws_region: str = "ca-central-1"
    s3_bucket: str | None = None
    cors_origins: str = "http://localhost:3040,http://127.0.0.1:3040"
    auth_jwt_secret: str = "change-this-development-secret-to-at-least-32-characters"
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
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    email_worker_concurrency: int = 10
    google_client_id: str = ""
    microsoft_client_id: str = ""
    apple_client_id: str = ""

    @field_validator("database_url", "redis_url", "opensearch_url", "rabbitmq_url")
    @classmethod
    def connection_url_must_not_be_blank(cls, value: str) -> str:
        if not value.strip(): raise ValueError("connection URL must not be blank")
        return value

    @property
    def cors_origin_list(self) -> list[str]: return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]