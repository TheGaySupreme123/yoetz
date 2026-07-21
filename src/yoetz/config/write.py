"""Atomic writers for service-owned nonsecret ``config.toml`` desired state."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from yoetz.config.models import (
    ConfigError,
    OwnerDeclaredEndpointConfig,
    ProviderProfileConfig,
    YoetzConfig,
)
from yoetz.config.paths import PathSafetyError, config_file_path, ensure_owner_only_dir
from yoetz.config.privacy import safe_privacy_bootstrap

__all__ = [
    "default_capability_profile",
    "official_openai_provider",
    "owner_declared_openai_provider",
    "render_config_toml",
    "write_config_toml",
    "write_provider_binding",
]

_OFFICIAL_CAPABILITY: Final = "openai-responses-structured-1"
_OWNER_CAPABILITY: Final = "openai-responses-structured-1"


def default_capability_profile() -> str:
    return _OFFICIAL_CAPABILITY


def official_openai_provider(
    *,
    model: str,
    endpoint_profile_version: str = "1.0.0",
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build the bundled official OpenAI Responses structural binding (no URL field)."""

    return ProviderProfileConfig(
        provider_id="openai",
        endpoint_profile_id="openai-responses",
        endpoint_profile_version=endpoint_profile_version,
        model=model,
        capability_profile=_OFFICIAL_CAPABILITY,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def owner_declared_openai_provider(
    *,
    model: str,
    https_origin: str,
    endpoint_profile_version: str = "1.0.0",
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build the exact owner-declared OpenAI-compatible Responses binding."""

    return ProviderProfileConfig(
        provider_id="openai-compatible",
        endpoint_profile_id="owner-declared-openai-responses",
        endpoint_profile_version=endpoint_profile_version,
        model=model,
        capability_profile=_OWNER_CAPABILITY,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        owner_declared_endpoint=OwnerDeclaredEndpointConfig(https_origin=https_origin),
    )


def _escape_basic(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _toml_str(value: str) -> str:
    return f'"{_escape_basic(value)}"'


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _emit_table(lines: list[str], header: str, mapping: Mapping[str, object]) -> None:
    lines.append(f"[{header}]")
    for key, raw in mapping.items():
        if isinstance(raw, bool):
            lines.append(f"{key} = {_toml_bool(raw)}")
        elif type(raw) is int:
            lines.append(f"{key} = {raw}")
        elif type(raw) is str:
            lines.append(f"{key} = {_toml_str(raw)}")
        else:
            raise ConfigError("config_value_invalid", safe_name=key[:256])
    lines.append("")


def render_config_toml(config: YoetzConfig) -> str:
    """Render a validated ``YoetzConfig`` as UTF-8 TOML text (no secrets).

    The first-run ``[privacy]`` bootstrap seed is omitted when it equals the safe default so
    loaders keep generation-1-only bootstrap semantics; durable privacy desired-state lives in
    the separate privacy desired-state path (ADR-014), never as silent authority here.
    """

    if type(config) is not YoetzConfig:
        raise TypeError("config_write_wrong_type")
    lines: list[str] = [
        f"schema_version = {_toml_str(config.schema_version)}",
        f"profile = {_toml_str(config.profile)}",
        "",
    ]
    storage: dict[str, object] = {"durability": config.storage.durability}
    if config.storage.data_dir is not None:
        storage["data_dir"] = str(config.storage.data_dir)
    _emit_table(lines, "storage", storage)
    _emit_table(
        lines,
        "verification",
        {
            "semantic": config.verification.semantic,
            "max_findings": config.verification.max_findings,
        },
    )
    _emit_table(
        lines,
        "logging",
        {"level": config.logging.level, "payloads": config.logging.payloads},
    )

    if config.privacy != safe_privacy_bootstrap():
        raise ConfigError("privacy_bootstrap_unsafe")

    if config.provider is not None:
        provider = config.provider
        _emit_table(
            lines,
            "provider",
            {
                "provider_id": provider.provider_id,
                "endpoint_profile_id": provider.endpoint_profile_id,
                "endpoint_profile_version": provider.endpoint_profile_version,
                "model": provider.model,
                "capability_profile": provider.capability_profile,
                "timeout_seconds": provider.timeout_seconds,
                "max_retries": provider.max_retries,
            },
        )
        if provider.owner_declared_endpoint is not None:
            _emit_table(
                lines,
                "provider.owner_declared_endpoint",
                {"https_origin": provider.owner_declared_endpoint.https_origin},
            )

    if config.local_model is not None:
        local = config.local_model
        _emit_table(
            lines,
            "local_model",
            {
                "profile_id": local.profile_id,
                "profile_version": local.profile_version,
                "endpoint_profile_id": local.endpoint_profile_id,
                "endpoint_profile_version": local.endpoint_profile_version,
                "model": local.model,
                "protocol_version": local.protocol_version,
                "judgment_schema_version": local.judgment_schema_version,
                "capability_digest": local.capability_digest,
                "timeout_seconds": local.timeout_seconds,
            },
        )

    text = "\n".join(lines).rstrip() + "\n"
    YoetzConfig.model_validate(tomllib.loads(text), strict=True)
    return text


def write_config_toml(config: YoetzConfig, path: Path | None = None) -> Path:
    """Atomically write validated nonsecret config to the service-owned path."""

    using_default = path is None
    target = config_file_path() if using_default else path
    assert target is not None
    if using_default:
        try:
            ensure_owner_only_dir(target.parent)
        except PathSafetyError as exc:
            raise ConfigError("config_value_invalid", safe_name=exc.reason_code) from exc
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    payload = render_config_toml(config).encode("utf-8")
    directory = target.parent
    descriptor, temporary_name = tempfile.mkstemp(prefix=".yoetz-config-", dir=directory)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ConfigError("config_value_invalid") from exc
    return target


def write_provider_binding(
    provider: ProviderProfileConfig,
    *,
    profile: str = "local-openai",
    path: Path | None = None,
    base: YoetzConfig | None = None,
) -> Path:
    """Merge a provider binding into service config and write it."""

    current = YoetzConfig() if base is None else base
    updated = current.model_copy(
        update={
            "profile": profile,
            "provider": provider,
            "local_model": None if profile == "local-openai" else current.local_model,
        }
    )
    return write_config_toml(updated, path=path)
