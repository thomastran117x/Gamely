from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource


# ${VAR} or ${VAR:-fallback}
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# Placeholder for an escaped "${" while substitution runs. YAML values never
# contain a NUL, so it cannot collide with real content.
_ESCAPE_MARKER = "\0"


class ConfigFileError(RuntimeError):
    """Raised when a configuration layer is malformed or unusable."""


class _Unresolved:
    """Marks a whole-value ${VAR} that had neither a value nor a fallback."""


def _resolve(match: re.Match[str]) -> str | None:
    """Substitution for one placeholder, or None when nothing supplies it."""
    name, fallback = match.group(1), match.group(2)
    value = os.environ.get(name)
    if value is not None:
        return value
    return fallback


def _interpolate(
    raw: str, origin: str, key: str, allow_absent: bool = True
) -> str | type[_Unresolved]:
    """Expand ${VAR} placeholders in one YAML scalar.

    A bare whole-value placeholder means "use this if the deployment supplies
    it", so an unset variable leaves the key absent and a lower layer wins. A
    placeholder embedded in a longer string is composing a value, and a hole in
    it is a bug -- falling through to a lower layer there would quietly swap in
    an unrelated value (a localhost CORS origin in production, say), so it
    raises. Write ${VAR:-fallback} to opt into a value for either case.
    """
    text = raw.replace("$${", _ESCAPE_MARKER)

    whole = _PLACEHOLDER.fullmatch(text)
    if whole is not None and allow_absent:
        resolved = _resolve(whole)
        if resolved is None:
            return _Unresolved
        return resolved.replace(_ESCAPE_MARKER, "${")

    def replace(match: re.Match[str]) -> str:
        value = _resolve(match)
        if value is None:
            raise ConfigFileError(
                f"{origin}: {key} embeds ${{{match.group(1)}}}, which is unset. "
                f"Write ${{{match.group(1)}:-fallback}} to supply a fallback."
            )
        return value

    return _PLACEHOLDER.sub(replace, text).replace(_ESCAPE_MARKER, "${")


def _expand(value: Any, origin: str, key: str) -> Any:
    """Interpolate a leaf value, recursing into sequences."""
    if isinstance(value, str):
        return _interpolate(value, origin, key)
    if isinstance(value, list):
        # A sequence element has no key of its own to leave absent, so an
        # unset placeholder there is always an error.
        return [
            _interpolate(item, origin, key, allow_absent=False)
            if isinstance(item, str)
            else item
            for item in value
        ]
    return value


def _flatten(
    node: dict[Any, Any],
    prefix: tuple[str, ...],
    origin: str,
    out: dict[str, tuple[str, Any]],
) -> None:
    """Collapse nested mappings onto underscore-joined settings field names.

    ``auth: {jwt: {issuer: x}}`` becomes ``auth_jwt_issuer``. Nesting groups
    related keys in the file and nothing more: no settings field is itself a
    mapping, so any mapping is a group. Each leaf keeps its dotted path so
    errors can point at what the author actually wrote.
    """
    for key, value in node.items():
        name = str(key)
        path = prefix + (name,)
        if isinstance(value, dict):
            _flatten(value, path, origin, out)
            continue
        flat = "_".join(path)
        dotted = ".".join(path)
        if flat in out:
            raise ConfigFileError(
                f"{origin}: {dotted} and {out[flat][0]} both resolve to "
                f"{flat}; keep one spelling"
            )
        out[flat] = (dotted, value)


def load_layer(path: Path, field_names: frozenset[str]) -> dict[str, Any]:
    """Read one YAML layer, keyed by settings field name.

    A missing file is an empty layer so that callers can always list every
    candidate path. Unknown keys raise rather than being ignored: unlike the
    `.env` layer, which uses ``extra="ignore"``, a typo here is a hard error.
    """
    if not path.is_file():
        return {}
    try:
        parsed: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ConfigFileError(f"{path} is not valid YAML: {error}") from error
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigFileError(f"{path} must contain a top-level mapping")

    leaves: dict[str, tuple[str, Any]] = {}
    _flatten(parsed, (), path.name, leaves)

    data: dict[str, Any] = {}
    unknown: list[str] = []
    for name, (dotted, value) in leaves.items():
        if name == "app_env":
            raise ConfigFileError(
                f"{path}: app_env is selected by the APP_ENV environment "
                "variable and must not be set in a configuration file"
            )
        if name not in field_names:
            unknown.append(dotted)
            continue
        resolved = _expand(value, path.name, dotted)
        if resolved is _Unresolved:
            # Leave the key absent so a lower layer or the field default
            # applies. A genuinely required value still fails validation.
            continue
        data[name] = resolved
    if unknown:
        raise ConfigFileError(f"{path}: unknown settings {sorted(unknown)}")
    return data


class YamlSettingsSource(PydanticBaseSettingsSource):
    """Settings source backed by a stack of YAML files."""

    def __init__(
        self, settings_cls: type[BaseSettings], paths: tuple[Path, ...]
    ) -> None:
        super().__init__(settings_cls)
        field_names = frozenset(settings_cls.model_fields)
        merged: dict[str, Any] = {}
        for path in paths:  # ordered lowest precedence first
            merged.update(load_layer(path, field_names))
        self._data = merged

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)
