from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.application.environment.environment_manager import (
    Settings,
    config_dir,
    resolve_app_env,
)
from src.application.environment.yaml_source import ConfigFileError


JWT_SECRET = "test-secret-with-at-least-thirty-two-characters"


@pytest.fixture
def config_layers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings at an empty, throwaway configuration directory."""
    monkeypatch.setenv("BACKEND_CONFIG_DIR", str(tmp_path))
    return tmp_path


def write_layer(directory: Path, name: str, body: str) -> None:
    (directory / f"{name}.yml").write_text(body, encoding="utf-8")


def settings_from(env_file: Path | None, **overrides: Any) -> Settings:
    """Settings reading ``env_file`` in place of the repository-root .env."""
    # pydantic-settings accepts _env_file at runtime, but it is not part of the
    # constructor signature mypy derives from the model fields.
    return Settings(_env_file=env_file, **overrides)  # type: ignore[call-arg]


@pytest.mark.parametrize("stage", ["dev", "test", "prod"])
def test_every_shipped_stage_produces_valid_settings(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the committed layers: a typo'd key or an out-of-range value here
    would otherwise only surface when that stage is deployed."""
    directory = config_dir()
    assert (directory / "default.yml").is_file()
    assert (directory / f"{stage}.yml").is_file()

    monkeypatch.setenv("APP_ENV", stage)
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    # prod.yml composes public URLs from this.
    monkeypatch.setenv("APP_DOMAIN", "games.example")

    # No env file, so a developer's root .env stays out of this check.
    assert settings_from(None).app_env == stage


def test_suite_runs_under_the_test_stage() -> None:
    assert resolve_app_env() == "test"
    # test.yml supplies the secret, so a bare Settings() is valid in the suite.
    assert Settings().auth_jwt_secret == JWT_SECRET


def test_layers_apply_in_precedence_order(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_layer(
        config_layers,
        "default",
        f"app_name: from-default\nauth_jwt_secret: {JWT_SECRET}\n",
    )

    assert Settings().app_name == "from-default"

    write_layer(config_layers, "test", "app_name: from-stage\n")
    assert Settings().app_name == "from-stage"

    monkeypatch.setenv("APP_NAME", "from-environment")
    assert Settings().app_name == "from-environment"

    assert Settings(app_name="from-init").app_name == "from-init"


def test_environment_layer_skips_dotenv_under_the_test_stage(
    config_layers: Path,
) -> None:
    # The suite runs with APP_ENV=test, so the repository-root .env must not be
    # consulted at all. A stale local .env would otherwise change results here.
    write_layer(config_layers, "default", f"auth_jwt_secret: {JWT_SECRET}\n")

    assert "env_file" in Settings.model_config
    assert Settings().app_name == "Games API"


def test_interpolation_reads_the_environment(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_DOMAIN", "games.example")
    write_layer(
        config_layers,
        "default",
        f'auth_jwt_secret: {JWT_SECRET}\ncors_origins: "https://${{APP_DOMAIN}},https://www.${{APP_DOMAIN}}"\n',
    )

    assert Settings().cors_origin_list == [
        "https://games.example",
        "https://www.games.example",
    ]


def test_interpolation_uses_the_fallback_when_unset(config_layers: Path) -> None:
    write_layer(
        config_layers,
        "default",
        f'auth_jwt_secret: {JWT_SECRET}\napp_name: "${{MISSING_NAME:-fallback-name}}"\n',
    )

    assert Settings().app_name == "fallback-name"


def test_unresolved_placeholder_falls_through_to_the_lower_layer(
    config_layers: Path,
) -> None:
    write_layer(
        config_layers,
        "default",
        f"auth_jwt_secret: {JWT_SECRET}\napp_name: from-default\n",
    )
    write_layer(config_layers, "test", 'app_name: "${MISSING_NAME}"\n')

    assert Settings().app_name == "from-default"


def test_double_dollar_escapes_a_literal_placeholder(config_layers: Path) -> None:
    write_layer(
        config_layers,
        "default",
        f'auth_jwt_secret: {JWT_SECRET}\napp_name: "$${{APP_NAME}}"\n',
    )

    assert Settings().app_name == "${APP_NAME}"


def test_unresolved_placeholder_inside_a_larger_value_is_rejected(
    config_layers: Path,
) -> None:
    # Falling through would quietly hand production the lower layer's value.
    write_layer(config_layers, "default", "cors_origins: http://localhost:3040\n")
    write_layer(config_layers, "test", 'cors_origins: "https://${MISSING_DOMAIN}"\n')

    with pytest.raises(ConfigFileError, match="MISSING_DOMAIN"):
        Settings()


def test_unknown_key_is_rejected(config_layers: Path) -> None:
    write_layer(config_layers, "default", "app_nmae: typo\n")

    with pytest.raises(ConfigFileError, match="app_nmae"):
        Settings()


def test_app_env_cannot_be_set_from_a_config_file(config_layers: Path) -> None:
    write_layer(config_layers, "default", "app_env: prod\n")

    with pytest.raises(ConfigFileError, match="APP_ENV"):
        Settings()


def test_non_mapping_layer_is_rejected(config_layers: Path) -> None:
    write_layer(config_layers, "default", "- one\n- two\n")

    with pytest.raises(ConfigFileError, match="top-level mapping"):
        Settings()


def test_missing_layers_are_tolerated(config_layers: Path) -> None:
    assert not list(config_layers.iterdir())

    assert Settings(auth_jwt_secret=JWT_SECRET).app_name == "Games API"


def test_unknown_app_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")

    with pytest.raises(ConfigFileError, match="staging"):
        Settings()


def test_settings_reads_environment_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@db:5432/test")
    monkeypatch.setenv("S3_BUCKET", "games-uploads")

    settings = Settings()

    assert settings.database_url.endswith("/test")
    assert settings.s3_bucket == "games-uploads"


def test_settings_rejects_non_string_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("DATABASE_URL", "")

    with pytest.raises(ValidationError):
        Settings()


def test_settings_rejects_missing_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_SECRET", "")
    with pytest.raises(ValidationError):
        Settings()


NESTED_DEFAULT = """
auth:
  jwt:
    secret: test-secret-with-at-least-thirty-two-characters
    issuer: nested-issuer
  refresh_cookie:
    name: nested_cookie
    secure: true
  email_filter:
    capacity: 4321
    rebuild:
      batch_size: 77
smtp:
  port: 2525
"""


def test_nested_keys_flatten_onto_field_names(config_layers: Path) -> None:
    write_layer(config_layers, "default", NESTED_DEFAULT)

    settings = Settings()

    assert settings.auth_jwt_issuer == "nested-issuer"
    assert settings.auth_refresh_cookie_name == "nested_cookie"
    assert settings.auth_refresh_cookie_secure is True
    assert settings.auth_email_filter_capacity == 4321
    assert settings.auth_email_filter_rebuild_batch_size == 77
    assert settings.smtp_port == 2525


def test_a_stage_layer_overrides_one_nested_leaf(config_layers: Path) -> None:
    write_layer(config_layers, "default", NESTED_DEFAULT)
    write_layer(config_layers, "test", "auth:\n  email_filter:\n    capacity: 9\n")

    settings = Settings()

    assert settings.auth_email_filter_capacity == 9
    # Siblings of the overridden leaf survive the merge.
    assert settings.auth_email_filter_rebuild_batch_size == 77
    assert settings.auth_jwt_issuer == "nested-issuer"


def test_nested_and_flat_spellings_of_one_field_collide(
    config_layers: Path,
) -> None:
    write_layer(
        config_layers,
        "default",
        "auth_jwt_issuer: flat\nauth:\n  jwt:\n    issuer: nested\n",
    )

    with pytest.raises(ConfigFileError, match="auth_jwt_issuer"):
        Settings()


def test_an_unknown_nested_key_is_reported_by_its_path(
    config_layers: Path,
) -> None:
    write_layer(config_layers, "default", "auth:\n  jwt:\n    isseur: typo\n")

    with pytest.raises(ConfigFileError, match=r"auth\.jwt\.isseur"):
        Settings()


def write_dotenv(directory: Path, body: str) -> Path:
    env_file = directory / "stage.env"
    env_file.write_text(body, encoding="utf-8")
    return env_file


def test_stage_in_dotenv_selects_the_matching_layer(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: the stage used to be read from os.environ alone, so APP_ENV in
    # .env reported prod while dev.yml's insecure cookies were loaded.
    monkeypatch.delenv("APP_ENV")
    write_layer(config_layers, "default", f"auth_jwt_secret: {JWT_SECRET}\n")
    write_layer(config_layers, "dev", "app_name: from-dev\n")
    write_layer(config_layers, "prod", "app_name: from-prod\n")
    env_file = write_dotenv(config_layers, "APP_ENV=prod\n")

    settings = settings_from(env_file)

    assert settings.app_env == "prod"
    assert settings.app_name == "from-prod"


def test_process_stage_outranks_dotenv_stage(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "dev")
    write_layer(config_layers, "default", f"auth_jwt_secret: {JWT_SECRET}\n")
    write_layer(config_layers, "dev", "app_name: from-dev\n")
    write_layer(config_layers, "prod", "app_name: from-prod\n")
    env_file = write_dotenv(config_layers, "APP_ENV=prod\n")

    settings = settings_from(env_file)

    assert settings.app_env == "dev"
    assert settings.app_name == "from-dev"


def test_constructor_stage_outranks_process_and_dotenv(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    write_layer(config_layers, "default", f"auth_jwt_secret: {JWT_SECRET}\n")
    write_layer(config_layers, "dev", "app_name: from-dev\n")
    env_file = write_dotenv(config_layers, "APP_ENV=prod\n")

    settings = settings_from(env_file, app_env="dev")

    assert settings.app_env == "dev"
    assert settings.app_name == "from-dev"


def test_placeholders_read_the_dotenv_file(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_ENV")
    monkeypatch.delenv("APP_DOMAIN", raising=False)
    write_layer(
        config_layers,
        "default",
        f'auth_jwt_secret: {JWT_SECRET}\ncors_origins: "https://${{APP_DOMAIN}}"\n',
    )
    env_file = write_dotenv(config_layers, "APP_DOMAIN=from-dotenv.example\n")

    assert settings_from(env_file).cors_origin_list == ["https://from-dotenv.example"]


def test_process_environment_outranks_dotenv_for_placeholders(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_ENV")
    monkeypatch.setenv("APP_DOMAIN", "from-process.example")
    write_layer(
        config_layers,
        "default",
        f'auth_jwt_secret: {JWT_SECRET}\ncors_origins: "https://${{APP_DOMAIN}}"\n',
    )
    env_file = write_dotenv(config_layers, "APP_DOMAIN=from-dotenv.example\n")

    assert settings_from(env_file).cors_origin_list == ["https://from-process.example"]


def test_placeholders_ignore_dotenv_under_the_test_stage(
    config_layers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("APP_DOMAIN", raising=False)
    write_layer(
        config_layers,
        "default",
        f'auth_jwt_secret: {JWT_SECRET}\napp_name: "${{APP_DOMAIN:-unseen}}"\n',
    )
    env_file = write_dotenv(config_layers, "APP_DOMAIN=leaked.example\n")

    assert settings_from(env_file).app_name == "unseen"
