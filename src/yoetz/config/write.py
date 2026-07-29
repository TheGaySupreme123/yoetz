"""Atomic writers for service-owned nonsecret ``config.toml`` desired state."""

from __future__ import annotations

import os
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
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
    "PROVIDER_PRESETS",
    "ProviderPreset",
    "anthropic_provider",
    "default_capability_profile",
    "fireworks_provider",
    "grok_provider",
    "gemini_provider",
    "google_gemini_provider",
    "official_openai_provider",
    "openrouter_provider",
    "owner_declared_openai_provider",
    "provider_preset",
    "render_config_toml",
    "vercel_ai_gateway_provider",
    "xai_provider",
    "write_config_toml",
    "write_provider_binding",
]

_OFFICIAL_CAPABILITY: Final = "openai-responses-structured-1"
_OWNER_CAPABILITY: Final = "openai-responses-structured-1"
_FIREWORKS_CAPABILITY: Final = "fireworks-responses-structured-1"
_ANTHROPIC_CAPABILITY: Final = "anthropic-openai-chat-completions-1"
_GEMINI_CAPABILITY: Final = "google-gemini-openai-chat-completions-1"
_OPENROUTER_CAPABILITY: Final = "openrouter-openai-chat-completions-1"
_XAI_CAPABILITY: Final = "xai-openai-chat-completions-1"
_VERCEL_AI_GATEWAY_CAPABILITY: Final = "vercel-ai-gateway-openai-responses-1"
_PROVIDER_CHOICE_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "openai": "official_openai",
        "official-openai": "official_openai",
        "claude": "anthropic",
        "anthropic-claude": "anthropic",
        "gemini": "google_gemini",
        "google": "google_gemini",
        "google-gemini": "google_gemini",
        "vercel-ai-gateway": "vercel_ai_gateway",
        "xai": "grok",
        "x-ai": "grok",
    }
)


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    """Exact nonsecret endpoint facts displayed by a bundled setup choice."""

    choice: str
    provider_id: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    capability_profile: str
    host: str
    base_path_prefix: str
    default_model: str
    api_style: str
    suggested_models: tuple[str, ...]

    def __post_init__(self) -> None:
        """Keep the reviewed setup catalog deterministic, bounded, and default-first."""

        suggestions = self.suggested_models
        if (
            not suggestions
            or len(suggestions) > 10
            or next(iter(suggestions), None) != self.default_model
            or len(set(suggestions)) != len(suggestions)
            or any(type(model) is not str or not model for model in suggestions)
        ):
            raise ValueError("provider_model_catalog_invalid")


PROVIDER_PRESETS: Final[Mapping[str, ProviderPreset]] = MappingProxyType(
    {
        "official_openai": ProviderPreset(
            "official_openai",
            "openai",
            "openai-responses",
            "1.0.0",
            _OFFICIAL_CAPABILITY,
            "api.openai.com",
            "/v1",
            "gpt-4.1-mini",
            "responses",
            (
                "gpt-4.1-mini",
                "gpt-5.6-sol",
                "gpt-5.6-terra",
                "gpt-5.6-luna",
                "gpt-5.5",
                "gpt-5.4-mini",
                "gpt-5.4-nano",
            ),
        ),
        "fireworks": ProviderPreset(
            "fireworks",
            "fireworks",
            "fireworks-responses",
            "1.0.0",
            _FIREWORKS_CAPABILITY,
            "api.fireworks.ai",
            "/inference/v1",
            "accounts/fireworks/models/qwen3-235b-a22b",
            "responses",
            (
                "accounts/fireworks/models/qwen3-235b-a22b",
                "accounts/fireworks/models/minimax-m3",
            ),
        ),
        "anthropic": ProviderPreset(
            "anthropic",
            "anthropic",
            "anthropic-openai-chat-completions",
            "1.0.0",
            _ANTHROPIC_CAPABILITY,
            "api.anthropic.com",
            "/v1",
            "claude-sonnet-4-6",
            "chat_completions",
            (
                "claude-sonnet-4-6",
                "claude-sonnet-5",
                "claude-opus-4-8",
                "claude-haiku-4-5-20251001",
            ),
        ),
        "google_gemini": ProviderPreset(
            "google_gemini",
            "google",
            "google-gemini-openai-chat-completions",
            "1.0.0",
            _GEMINI_CAPABILITY,
            "generativelanguage.googleapis.com",
            "/v1beta/openai",
            "gemini-3.5-flash",
            "chat_completions",
            (
                "gemini-3.5-flash",
                "gemini-3.6-flash",
                "gemini-3.5-flash-lite",
            ),
        ),
        "openrouter": ProviderPreset(
            "openrouter",
            "openrouter",
            "openrouter-openai-chat-completions",
            "1.0.0",
            _OPENROUTER_CAPABILITY,
            "openrouter.ai",
            "/api/v1",
            "openai/gpt-5.2",
            "chat_completions",
            (
                "openai/gpt-5.2",
                "x-ai/grok-4.5",
                "google/gemini-3.6-flash",
                "anthropic/claude-sonnet-5",
                "openai/gpt-5.6-terra",
            ),
        ),
        "grok": ProviderPreset(
            "grok",
            "xai",
            "xai-openai-chat-completions",
            "1.0.0",
            _XAI_CAPABILITY,
            "api.x.ai",
            "/v1",
            "grok-4.5",
            "chat_completions",
            (
                "grok-4.5",
                "grok-4.3",
                "grok-4.20-0309-reasoning",
                "grok-4.20-0309-non-reasoning",
            ),
        ),
        "vercel_ai_gateway": ProviderPreset(
            "vercel_ai_gateway",
            "vercel-ai-gateway",
            "vercel-ai-gateway-openai-responses",
            "1.0.0",
            _VERCEL_AI_GATEWAY_CAPABILITY,
            "ai-gateway.vercel.sh",
            "/v1",
            "anthropic/claude-sonnet-4-6",
            "responses",
            (
                "anthropic/claude-sonnet-4-6",
                "openai/gpt-5.4",
                "xai/grok-4.5",
                "google/gemini-3.6-flash",
            ),
        ),
    }
)


def provider_preset(choice: str) -> ProviderPreset:
    """Return one closed bundled preset or a bounded configuration error."""

    try:
        normalized = choice.strip().lower()
        canonical = _PROVIDER_CHOICE_ALIASES.get(normalized, normalized)
        return PROVIDER_PRESETS[canonical]
    except (AttributeError, KeyError, TypeError) as exc:
        raise ConfigError("config_value_invalid") from exc


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


def _bundled_provider(
    choice: str,
    *,
    model: str,
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    preset = provider_preset(choice)
    if not model:
        raise ConfigError("config_value_invalid")
    return ProviderProfileConfig(
        provider_id=preset.provider_id,
        endpoint_profile_id=preset.endpoint_profile_id,
        endpoint_profile_version=preset.endpoint_profile_version,
        model=model,
        capability_profile=preset.capability_profile,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def fireworks_provider(
    *,
    model: str,
    endpoint_profile_version: str = "1.0.0",
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build the reviewed fixed Fireworks Responses endpoint binding."""

    return ProviderProfileConfig(
        provider_id="fireworks",
        endpoint_profile_id="fireworks-responses",
        endpoint_profile_version=endpoint_profile_version,
        model=model,
        capability_profile=_FIREWORKS_CAPABILITY,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )


def anthropic_provider(
    *,
    model: str,
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build Anthropic's exact OpenAI-compatible Chat Completions binding."""

    return _bundled_provider(
        "anthropic", model=model, timeout_seconds=timeout_seconds, max_retries=max_retries
    )


def google_gemini_provider(
    *,
    model: str,
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build Google's exact Gemini OpenAI-compatible Chat Completions binding."""

    return _bundled_provider(
        "google_gemini", model=model, timeout_seconds=timeout_seconds, max_retries=max_retries
    )


gemini_provider = google_gemini_provider


def openrouter_provider(
    *,
    model: str,
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build OpenRouter's exact OpenAI-compatible Chat Completions binding."""

    return _bundled_provider(
        "openrouter", model=model, timeout_seconds=timeout_seconds, max_retries=max_retries
    )


def grok_provider(
    *,
    model: str,
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build xAI's exact Grok OpenAI-compatible Chat Completions binding."""

    return _bundled_provider(
        "grok", model=model, timeout_seconds=timeout_seconds, max_retries=max_retries
    )


xai_provider = grok_provider


def vercel_ai_gateway_provider(
    *,
    model: str,
    timeout_seconds: int = 60,
    max_retries: int = 2,
) -> ProviderProfileConfig:
    """Build Vercel AI Gateway's exact OpenAI-compatible Responses binding."""

    return _bundled_provider(
        "vercel_ai_gateway", model=model, timeout_seconds=timeout_seconds, max_retries=max_retries
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
