"""Hermetic service-start configuration loading and source precedence."""

from __future__ import annotations

import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from pydantic import ValidationError

from yoetz.config.models import ConfigError, YoetzConfig
from yoetz.config.paths import config_file_path

__all__ = [
    "ENV_PREFIX",
    "MinimalConfig",
    "default_config_file_path",
    "load_config",
    "parse_minimal_safe_config",
]

ENV_PREFIX: Final = "YOETZ_"
_MAX_CONFIG_BYTES: Final = 65_536
_INTEGER = re.compile(r"^-?[0-9]+$", re.ASCII)
_SECRET_TOKENS: Final = ("api_key", "apikey", "token", "secret", "password")
_VCS_MARKERS: Final = frozenset({".git", ".hg", ".svn", ".jj"})

type _LeafPath = tuple[str, ...]

_ENV_TO_LEAF: Final[dict[str, _LeafPath | None]] = {
    "YOETZ_CONFIG": None,
    # Documented presentation control, recognized here so strict config loading cannot mistake
    # the prompt-loop opt-out for a service setting. It maps to no configuration leaf.
    "YOETZ_TUI": None,
    "YOETZ_PROFILE": ("profile",),
    "YOETZ_STORAGE_DATA_DIR": ("storage", "data_dir"),
    "YOETZ_STORAGE_DURABILITY": ("storage", "durability"),
    "YOETZ_VERIFICATION_SEMANTIC": ("verification", "semantic"),
    "YOETZ_VERIFICATION_MAX_FINDINGS": ("verification", "max_findings"),
    "YOETZ_LOG_LEVEL": ("logging", "level"),
    "YOETZ_PROVIDER_ID": ("provider", "provider_id"),
    "YOETZ_PROVIDER_ENDPOINT_PROFILE_ID": ("provider", "endpoint_profile_id"),
    "YOETZ_PROVIDER_ENDPOINT_PROFILE_VERSION": (
        "provider",
        "endpoint_profile_version",
    ),
    "YOETZ_PROVIDER_MODEL": ("provider", "model"),
    "YOETZ_PROVIDER_TIMEOUT_SECONDS": ("provider", "timeout_seconds"),
}
_OVERRIDE_TO_LEAF: Final[dict[str, _LeafPath | None]] = {
    "config": None,
    "profile": ("profile",),
    "storage.data_dir": ("storage", "data_dir"),
    "storage.durability": ("storage", "durability"),
    "verification.semantic": ("verification", "semantic"),
    "verification.max_findings": ("verification", "max_findings"),
    "logging.level": ("logging", "level"),
    "provider.provider_id": ("provider", "provider_id"),
    "provider.endpoint_profile_id": ("provider", "endpoint_profile_id"),
    "provider.endpoint_profile_version": ("provider", "endpoint_profile_version"),
    "provider.model": ("provider", "model"),
    "provider.timeout_seconds": ("provider", "timeout_seconds"),
}
_INTEGER_LEAVES: Final = frozenset(
    {
        ("verification", "max_findings"),
        ("provider", "timeout_seconds"),
    }
)
_PATH_LEAVES: Final = frozenset({("storage", "data_dir")})
_PROFILES: Final = frozenset(
    {"strict-local", "local-openai", "codex-subscription", "test-fake", "release-probe"}
)
_LOG_LEVELS: Final = frozenset({"debug", "info", "warning", "error"})


@dataclass(frozen=True, slots=True)
class MinimalConfig:
    profile: Literal[
        "strict-local", "local-openai", "codex-subscription", "test-fake", "release-probe"
    ]
    data_dir: Path | None
    log_level: Literal["debug", "info", "warning", "error"]
    config_path_used: Path | None

    def __post_init__(self) -> None:
        if self.profile not in _PROFILES:
            raise ConfigError("config_value_invalid")
        if self.log_level not in _LOG_LEVELS:
            raise ConfigError("config_value_invalid")


def default_config_file_path() -> Path:
    """Return the sole platform-native default config path."""

    return config_file_path()


def _key_names(mapping: Mapping[str, str]) -> tuple[str, ...]:
    try:
        raw = tuple(mapping.keys())
    except Exception as exc:
        raise ConfigError("config_value_invalid") from exc
    if any(type(key) is not str for key in raw):
        raise ConfigError("config_value_invalid")
    return tuple(sorted(raw))


def _secret_name(name: str) -> bool:
    return any(token in name.casefold() for token in _SECRET_TOKENS)


def _validated_source_values(
    source: Mapping[str, str],
    *,
    names: Mapping[str, _LeafPath | None],
    prefix_only: bool,
) -> dict[str, str]:
    keys = _key_names(source)
    considered = tuple(key for key in keys if not prefix_only or key.startswith(ENV_PREFIX))
    secret = tuple(key for key in considered if _secret_name(key))
    if secret:
        reason = "secret_env_forbidden" if prefix_only else "secret_config_override_forbidden"
        raise ConfigError(reason, safe_name=secret[0][:256])
    unknown = tuple(key for key in considered if key not in names)
    if unknown:
        reason = "unknown_config_env_var" if prefix_only else "unknown_config_override"
        raise ConfigError(reason, safe_name=unknown[0][:256])

    values: dict[str, str] = {}
    for key in considered:
        try:
            value = source[key]
        except Exception as exc:
            raise ConfigError("config_value_invalid", safe_name=key[:256]) from exc
        if type(value) is not str:
            raise ConfigError("config_value_invalid", safe_name=key[:256])
        values[key] = value
    return values


def _selected_config_path(
    config_path: Path | None,
    env_values: Mapping[str, str],
    override_values: Mapping[str, str],
) -> tuple[Path, bool]:
    if config_path is not None:
        return config_path, True
    override = override_values.get("config", "")
    if override:
        return Path(override), True
    environment = env_values.get("YOETZ_CONFIG", "")
    if environment:
        return Path(environment), True
    return default_config_file_path(), False


def _read_config(path: Path) -> tuple[dict[str, object], bool]:
    try:
        with path.open("rb") as source:
            data = source.read(_MAX_CONFIG_BYTES + 1)
    except FileNotFoundError:
        return {}, False
    except OSError as exc:
        raise ConfigError("config_file_unreadable") from exc
    if len(data) > _MAX_CONFIG_BYTES:
        raise ConfigError("config_file_too_large")
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        line = getattr(exc, "lineno", None)
        column = getattr(exc, "colno", None)
        raise ConfigError(
            "config_toml_invalid",
            line=line if type(line) is int and line > 0 else None,
            column=column if type(column) is int and column > 0 else None,
        ) from None
    return cast(dict[str, object], parsed), True


def _in_repository(path: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
        return any(
            (ancestor / marker).exists()
            for ancestor in (resolved.parent, *resolved.parent.parents)
            for marker in _VCS_MARKERS
        )
    except OSError:
        return False


def _emit_explicit_project_config() -> None:
    sys.stderr.write('{"reason":"explicit_project_config"}\n')


def _mapping_section(source: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = source.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError("config_value_invalid", safe_name=name)
    return cast(Mapping[str, object], value)


def _file_leaf(source: Mapping[str, object], leaf: _LeafPath) -> tuple[object, bool]:
    if len(leaf) == 1:
        return (source[leaf[0]], True) if leaf[0] in source else (None, False)
    section = _mapping_section(source, leaf[0])
    return (section[leaf[1]], True) if leaf[1] in section else (None, False)


def _parse_string_leaf(leaf: _LeafPath, value: str) -> object:
    if leaf in _INTEGER_LEAVES:
        if _INTEGER.fullmatch(value) is None:
            raise ConfigError("config_value_invalid")
        return int(value, 10)
    if leaf in _PATH_LEAVES:
        return Path(value)
    return value


def _set_leaf(target: dict[str, object], leaf: _LeafPath, value: object) -> None:
    if len(leaf) == 1:
        target[leaf[0]] = value
        return
    existing = target.get(leaf[0])
    if existing is None:
        section: dict[str, object] = {}
    elif isinstance(existing, Mapping):
        section = dict(cast(Mapping[str, object], existing))
    else:
        raise ConfigError("config_value_invalid", safe_name=leaf[0])
    section[leaf[1]] = value
    target[leaf[0]] = section


def _merge_selected(
    file_values: Mapping[str, object],
    env_values: Mapping[str, str],
    override_values: Mapping[str, str],
) -> dict[str, object]:
    merged = dict(file_values)
    for override_name, leaf in _OVERRIDE_TO_LEAF.items():
        if leaf is None:
            continue
        env_name = next(name for name, candidate in _ENV_TO_LEAF.items() if candidate == leaf)
        override = override_values.get(override_name, "")
        environment = env_values.get(env_name, "")
        if override:
            _set_leaf(merged, leaf, _parse_string_leaf(leaf, override))
        elif environment:
            _set_leaf(merged, leaf, _parse_string_leaf(leaf, environment))

    file_data_dir, present = _file_leaf(merged, ("storage", "data_dir"))
    if present and type(file_data_dir) is str:
        _set_leaf(merged, ("storage", "data_dir"), Path(file_data_dir))
    return merged


def _reject_file_release_probe(file_values: Mapping[str, object]) -> None:
    if file_values.get("profile") == "release-probe":
        raise ConfigError("release_probe_not_a_user_profile")


def load_config(
    service_overrides: Mapping[str, str],
    env: Mapping[str, str],
    config_path: Path | None,
) -> YoetzConfig:
    """Load exactly one service config using leaf-wise trusted precedence."""

    env_values = _validated_source_values(env, names=_ENV_TO_LEAF, prefix_only=True)
    override_values = _validated_source_values(
        service_overrides, names=_OVERRIDE_TO_LEAF, prefix_only=False
    )
    selected_path, explicit = _selected_config_path(config_path, env_values, override_values)
    file_values, read = _read_config(selected_path)
    if read:
        _reject_file_release_probe(file_values)
    if explicit and read and _in_repository(selected_path):
        _emit_explicit_project_config()
    merged = _merge_selected(file_values, env_values, override_values)
    try:
        return YoetzConfig.model_validate(merged, strict=True)
    except ConfigError:
        raise
    except ValidationError as exc:
        raise ConfigError("config_value_invalid") from exc
    except Exception as exc:
        raise ConfigError("config_value_invalid") from exc


def _minimal_file_value(source: Mapping[str, object], leaf: _LeafPath, default: object) -> object:
    value, present = _file_leaf(source, leaf)
    return value if present else default


def parse_minimal_safe_config(
    env: Mapping[str, str], service_overrides: Mapping[str, str]
) -> MinimalConfig:
    """Tolerantly resolve the startup-safe profile, data path, and log level."""

    env_values = _validated_source_values(env, names=_ENV_TO_LEAF, prefix_only=True)
    override_values = _validated_source_values(
        service_overrides, names=_OVERRIDE_TO_LEAF, prefix_only=False
    )
    selected_path, explicit = _selected_config_path(None, env_values, override_values)
    file_values, read = _read_config(selected_path)
    if read:
        _reject_file_release_probe(file_values)
    if explicit and read and _in_repository(selected_path):
        _emit_explicit_project_config()

    profile: object = _minimal_file_value(file_values, ("profile",), "strict-local")
    env_profile = env_values.get("YOETZ_PROFILE", "")
    override_profile = override_values.get("profile", "")
    if override_profile:
        profile = override_profile
    elif env_profile:
        profile = env_profile
    if type(profile) is not str or profile not in _PROFILES:
        raise ConfigError("config_value_invalid")

    data_dir: object = _minimal_file_value(file_values, ("storage", "data_dir"), None)
    env_data_dir = env_values.get("YOETZ_STORAGE_DATA_DIR", "")
    override_data_dir = override_values.get("storage.data_dir", "")
    if override_data_dir:
        data_dir = Path(override_data_dir)
    elif env_data_dir:
        data_dir = Path(env_data_dir)
    elif type(data_dir) is str:
        data_dir = Path(data_dir)
    if data_dir is not None and not isinstance(data_dir, Path):
        raise ConfigError("config_value_invalid")

    log_level: object = _minimal_file_value(file_values, ("logging", "level"), "info")
    env_log = env_values.get("YOETZ_LOG_LEVEL", "")
    override_log = override_values.get("logging.level", "")
    if override_log:
        log_level = override_log
    elif env_log:
        log_level = env_log
    if type(log_level) is not str or log_level not in _LOG_LEVELS:
        raise ConfigError("config_value_invalid")

    return MinimalConfig(
        profile=cast(
            Literal[
                "strict-local",
                "local-openai",
                "codex-subscription",
                "test-fake",
                "release-probe",
            ],
            profile,
        ),
        data_dir=data_dir,
        log_level=cast(Literal["debug", "info", "warning", "error"], log_level),
        config_path_used=selected_path if read else None,
    )
