import os
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values
from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from src.application.environment.yaml_source import ConfigFileError, YamlSettingsSource


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ROOT_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"

AppEnv = Literal["dev", "test", "prod"]
_APP_ENVS: tuple[AppEnv, ...] = ("dev", "test", "prod")


def _validate_app_env(value: str, origin: str) -> AppEnv:
    normalised = value.strip().lower()
    if normalised not in _APP_ENVS:
        raise ConfigFileError(
            f"{origin} must be one of {', '.join(_APP_ENVS)} (got {value!r})"
        )
    return normalised


def _stage_from(source: PydanticBaseSettingsSource) -> str | None:
    value = source().get("app_env")
    return value if isinstance(value, str) else None


def resolve_app_env(*sources: PydanticBaseSettingsSource) -> AppEnv:
    """Return the active stage, honouring the same precedence as every setting.

    The stage has to be known before the YAML layer can be chosen, so it cannot
    simply be read off the constructed Settings. Reading it from os.environ
    alone is not enough either: the documented workflow puts APP_ENV in the root
    .env, which pydantic parses but never exports to the process environment. A
    stage found only there would leave Settings.app_env reporting one stage
    while the layer of another was loaded. Callers pass the candidate sources
    highest precedence first; the winner is then pinned onto the field by
    StageSource so the two can never disagree.
    """
    for source in sources:
        candidate = _stage_from(source)
        if candidate is not None:
            return _validate_app_env(candidate, "app_env")
    from_environment = os.environ.get("APP_ENV")
    if from_environment is not None:
        return _validate_app_env(from_environment, "APP_ENV")
    return "dev"


def dotenv_files(source: PydanticBaseSettingsSource) -> tuple[Path, ...]:
    """The .env files the dotenv source will read, in pydantic's order.

    Taken from the source rather than the module constant so that
    Settings(_env_file=...) moves placeholders and settings together.
    """
    if not isinstance(source, DotEnvSettingsSource) or source.env_file is None:
        return ()
    configured = source.env_file
    if isinstance(configured, (str, Path)):
        return (Path(configured),)
    return tuple(Path(item) for item in configured)


def interpolation_env(files: tuple[Path, ...]) -> dict[str, str]:
    """Variables visible to ${VAR} placeholders in the YAML layers.

    Placeholders compose deployment-supplied values, and the documented home for
    those is the root .env -- but pydantic only reads that file for recognised
    settings fields, so a composition variable like APP_DOMAIN living there
    would otherwise be invisible. Read the files directly (later files win, as
    in pydantic), then let the real process environment win over all of them,
    matching the precedence used for the settings themselves.
    """
    values: dict[str, str] = {}
    for path in files:
        if path.is_file():
            values.update(
                {
                    key: value
                    for key, value in dotenv_values(path).items()
                    if value is not None
                }
            )
    values.update(os.environ)
    return values


class StageSource(PydanticBaseSettingsSource):
    """Pins app_env to the stage whose configuration layer was actually loaded."""

    def __init__(self, settings_cls: type[BaseSettings], app_env: AppEnv) -> None:
        super().__init__(settings_cls)
        self.app_env: AppEnv = app_env

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return (self.app_env if field_name == "app_env" else None), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {"app_env": self.app_env}


def config_dir() -> Path:
    """Directory holding the YAML layers.

    Defaults to ``backend/config``, which resolves to ``/app/config`` inside the
    container because the image keeps the same ``src`` layout under /app.
    """
    override = os.environ.get("BACKEND_CONFIG_DIR")
    return Path(override) if override else _BACKEND_ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ROOT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )
    # Pinned by StageSource to the stage whose layer was loaded.
    app_env: AppEnv = "dev"
    app_name: str = "Games API"
    database_url: str = "postgresql+asyncpg://games:games@127.0.0.1:5432/games"
    redis_url: str = "redis://127.0.0.1:6379/0"
    opensearch_url: str = "http://127.0.0.1:9200"
    rabbitmq_url: str = "amqp://games:games@127.0.0.1:5672/"
    aws_region: str = "ca-central-1"
    s3_bucket: str | None = None
    cors_origins: str = "http://localhost:3040,http://127.0.0.1:3040"
    auth_jwt_secret: str = ""
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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Highest precedence first, matching the documented order.
        app_env = resolve_app_env(init_settings, env_settings, dotenv_settings)
        stage = StageSource(settings_cls, app_env)
        directory = config_dir()
        # APP_ENV=test drops the .env layer, so placeholders must not see it
        # either; the suite has to resolve identically on every machine.
        yaml_settings = YamlSettingsSource(
            settings_cls,
            (directory / "default.yml", directory / f"{app_env}.yml"),
            interpolation_env(
                () if app_env == "test" else dotenv_files(dotenv_settings)
            ),
        )
        if app_env == "test":
            # Skip .env so a developer's root file cannot leak into the suite.
            return (stage, init_settings, env_settings, yaml_settings)
        return (stage, init_settings, env_settings, dotenv_settings, yaml_settings)

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
