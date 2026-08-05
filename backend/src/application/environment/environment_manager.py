from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Games API"
    database_url: str = "postgresql+asyncpg://games:games@127.0.0.1:5432/games"
    redis_url: str = "redis://127.0.0.1:6379/0"
    opensearch_url: str = "http://127.0.0.1:9200"
    rabbitmq_url: str = "amqp://games:games@127.0.0.1:5672/"
    aws_region: str = "ca-central-1"
    s3_bucket: str | None = None
    cors_origins: str = "http://localhost:3040,http://127.0.0.1:3040"

    @field_validator("database_url", "redis_url", "opensearch_url", "rabbitmq_url")
    @classmethod
    def connection_url_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("connection URL must not be blank")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]
