"""Strict, frozen service-owned configuration models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from yoetz.domain.privacy import ProviderDataUseProfile
from yoetz.protocol.models import MAX_FINDINGS_DEFAULT, MAX_FINDINGS_LIMIT

__all__ = [
    "OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID",
    "OWNER_DECLARED_ENDPOINT_PROFILE_ID",
    "OWNER_DECLARED_PROVIDER_ID",
    "PROFILE_CAPABILITIES",
    "ConfigError",
    "LocalModelProfileConfig",
    "LoggingConfig",
    "NetworkPolicy",
    "ObservationConfig",
    "OwnerDeclaredEndpointConfig",
    "PrivacyBootstrapConfig",
    "ProfileCapabilities",
    "ProviderDataUseProfile",
    "ProviderProfileConfig",
    "SemanticPolicy",
    "StorageConfig",
    "VerificationConfig",
    "YoetzConfig",
    "parse_https_origin",
]

OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID: Final = "openai-responses"
OWNER_DECLARED_ENDPOINT_PROFILE_ID: Final = "owner-declared-openai-responses"
OWNER_DECLARED_PROVIDER_ID: Final = "openai-compatible"

_MODEL_CONFIG: Final = ConfigDict(
    strict=True,
    extra="forbid",
    frozen=True,
    validate_default=True,
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$", re.ASCII)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_SECRET_KEY_TOKENS: Final = ("api_key", "apikey", "token", "secret", "password")
_LOCAL_LOCATOR_KEYS: Final = frozenset(
    {
        "arguments",
        "base_url",
        "command",
        "discovery_mode",
        "download_source",
        "environment",
        "executable",
        "headers",
        "host",
        "options",
        "port",
        "socket",
        "socket_path",
        "url",
    }
)
_CONFIG_ERROR_REASONS: Final = frozenset(
    {
        "config_schema_unsupported",
        "config_value_invalid",
        "durability_unsupported",
        "external_profile_forbids_local_model",
        "https_origin_invalid",
        "local_model_locator_forbidden",
        "max_findings_out_of_range",
        "owner_declared_endpoint_forbidden",
        "owner_declared_endpoint_required",
        "payload_logging_forbidden",
        "privacy_bootstrap_unsafe",
        "provider_required_for_semantic",
        "secret_config_override_forbidden",
        "secret_in_config",
        "strict_local_forbids_provider",
        "test_fake_forbids_local_model",
        "test_fake_forbids_provider",
        "unknown_config_key",
    }
)
_OWNER_DECLARED_FORBIDDEN_KEYS: Final = frozenset(
    {
        "api_key",
        "authorization",
        "base_url",
        "credential",
        "headers",
        "host",
        "http_origin",
        "path",
        "port",
        "query",
        "token",
        "url",
    }
)
_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$",
    re.ASCII,
)


class ConfigError(Exception):
    """A bounded reviewed configuration failure."""

    reason_code: str
    safe_name: str | None
    line: int | None
    column: int | None

    def __init__(
        self,
        reason_code: str,
        *,
        safe_name: str | None = None,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        # Loader-owned reason codes extend this closed model-owned set.
        if type(reason_code) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason_code):
            raise ValueError("config_reason_code_invalid")
        if reason_code not in _CONFIG_ERROR_REASONS and not reason_code.startswith(
            ("config_", "secret_env_", "unknown_config_", "release_probe_")
        ):
            raise ValueError("config_reason_code_invalid")
        if safe_name is not None and (type(safe_name) is not str or len(safe_name) > 256):
            raise ValueError("config_safe_name_invalid")
        if line is not None and (type(line) is not int or line < 1):
            raise ValueError("config_line_invalid")
        if column is not None and (type(column) is not int or column < 1):
            raise ValueError("config_column_invalid")
        self.reason_code = reason_code
        self.safe_name = safe_name
        self.line = line
        self.column = column
        super().__init__(reason_code)


def _validation_reason(error: ValidationError) -> str:
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        if item["type"] == "extra_forbidden":
            return "unknown_config_key"
    return "config_value_invalid"


class StrictConfigModel(BaseModel):
    model_config = _MODEL_CONFIG

    def __init__(self, /, **data: object) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise ConfigError(_validation_reason(exc)) from None


def _mapping(value: object) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ConfigError("config_value_invalid")
    return cast(Mapping[object, object], value)


def _keys(value: object) -> tuple[object, ...]:
    source = _mapping(value)
    try:
        return tuple(source.keys())
    except Exception as exc:
        raise ConfigError("config_value_invalid") from exc


def _reject_unknown(value: object, allowed: frozenset[str]) -> None:
    unknown = sorted(
        (key for key in _keys(value) if type(key) is not str or key not in allowed),
        key=lambda key: str(key),
    )
    if unknown:
        key = unknown[0]
        raise ConfigError(
            "unknown_config_key", safe_name=key if type(key) is str and len(key) <= 256 else None
        )


def _secret_key(key: object) -> bool:
    return type(key) is str and any(token in key.casefold() for token in _SECRET_KEY_TOKENS)


def _scan_secret_keys(value: object) -> None:
    matches: list[str] = []
    for key in _keys(value):
        if type(key) is str and _secret_key(key):
            matches.append(key)
    matches.sort()
    if matches:
        raise ConfigError("secret_in_config", safe_name=matches[0][:256])


def _validate_identifier(value: object) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ConfigError("config_value_invalid")


def parse_https_origin(value: object) -> tuple[str, int]:
    """Validate a constrained HTTPS origin (scheme+host+optional port only).

    Rejects ``http``, userinfo, path (other than empty/``/``), query, fragment, and credentials.
    Returns ``(hostname, port)`` with default port ``443``.
    """

    if type(value) is not str or not 8 <= len(value) <= 512:
        raise ConfigError("https_origin_invalid")
    try:
        parsed = urlparse(value)
        # urllib raises ValueError lazily from .port for out-of-range / non-numeric ports
        port = 443 if parsed.port is None else parsed.port
    except ValueError as exc:
        raise ConfigError("https_origin_invalid") from exc
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or type(parsed.hostname) is not str
        or not parsed.hostname
        or _HOSTNAME.fullmatch(parsed.hostname) is None
    ):
        raise ConfigError("https_origin_invalid")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError("https_origin_invalid")
    return parsed.hostname.casefold(), port


class OwnerDeclaredEndpointConfig(StrictConfigModel):
    """Owner-supplied HTTPS origin for the exact owner-declared Responses profile kind."""

    https_origin: str

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        _scan_secret_keys(value)
        source = _mapping(value)
        forbidden = sorted(
            key
            for key in _keys(value)
            if type(key) is str and key.casefold() in _OWNER_DECLARED_FORBIDDEN_KEYS
        )
        if forbidden:
            raise ConfigError("unknown_config_key", safe_name=forbidden[0][:256])
        _reject_unknown(value, frozenset({"https_origin"}))
        if "https_origin" not in source:
            raise ConfigError("https_origin_invalid")
        host, port = parse_https_origin(source["https_origin"])
        origin = f"https://{host}" if port == 443 else f"https://{host}:{port}"
        return {"https_origin": origin}

    @property
    def host(self) -> str:
        return parse_https_origin(self.https_origin)[0]

    @property
    def port(self) -> int:
        return parse_https_origin(self.https_origin)[1]


class StorageConfig(StrictConfigModel):
    data_dir: Path | None = None
    durability: Literal["full"] = "full"

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        _reject_unknown(value, frozenset({"data_dir", "durability"}))
        source = _mapping(value)
        if "durability" in source and source["durability"] != "full":
            raise ConfigError("durability_unsupported")
        return value


class VerificationConfig(StrictConfigModel):
    semantic: Literal["disabled", "optional", "required"] = "optional"
    max_findings: int = MAX_FINDINGS_DEFAULT

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        _reject_unknown(value, frozenset({"semantic", "max_findings"}))
        source = _mapping(value)
        if "max_findings" in source:
            maximum = source["max_findings"]
            if type(maximum) is not int or not 1 <= maximum <= MAX_FINDINGS_LIMIT:
                raise ConfigError("max_findings_out_of_range")
        return value


class ObservationConfig(StrictConfigModel):
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        _reject_unknown(value, frozenset({"enabled"}))
        return value


class LoggingConfig(StrictConfigModel):
    level: Literal["debug", "info", "warning", "error"] = "info"
    payloads: bool = False

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        _reject_unknown(value, frozenset({"level", "payloads"}))
        source = _mapping(value)
        if "payloads" in source and source["payloads"] is not False:
            raise ConfigError("payload_logging_forbidden")
        return value


class ProviderProfileConfig(StrictConfigModel):
    provider_id: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    model: str
    capability_profile: str
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_retries: int = Field(default=2, ge=0, le=2)
    owner_declared_endpoint: OwnerDeclaredEndpointConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        _scan_secret_keys(value)
        # Free-form locators stay forbidden on the ordinary provider surface (ADR-006/014).
        locator_keys = sorted(
            key
            for key in _keys(value)
            if type(key) is str
            and key.casefold() in _LOCAL_LOCATOR_KEYS.union({"https_origin", "http_origin"})
        )
        if locator_keys:
            raise ConfigError("unknown_config_key", safe_name=locator_keys[0][:256])
        allowed = frozenset(
            {
                "provider_id",
                "endpoint_profile_id",
                "endpoint_profile_version",
                "model",
                "capability_profile",
                "timeout_seconds",
                "max_retries",
                "owner_declared_endpoint",
            }
        )
        _reject_unknown(value, allowed)
        source = _mapping(value)
        for key in (
            "provider_id",
            "endpoint_profile_id",
            "endpoint_profile_version",
            "model",
            "capability_profile",
        ):
            if key in source:
                _validate_identifier(source[key])
        return value

    @model_validator(mode="after")
    def _validate_owner_declared(self) -> ProviderProfileConfig:
        owner_declared = self.endpoint_profile_id == OWNER_DECLARED_ENDPOINT_PROFILE_ID
        if owner_declared and self.owner_declared_endpoint is None:
            raise ConfigError("owner_declared_endpoint_required")
        if not owner_declared and self.owner_declared_endpoint is not None:
            raise ConfigError("owner_declared_endpoint_forbidden")
        return self


class LocalModelProfileConfig(StrictConfigModel):
    profile_id: str
    profile_version: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    model: str
    protocol_version: str
    judgment_schema_version: str
    capability_digest: str
    timeout_seconds: int = Field(default=60, ge=1, le=300)

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        _scan_secret_keys(value)
        raw_keys = _keys(value)
        locator_keys = sorted(
            key for key in raw_keys if type(key) is str and key.casefold() in _LOCAL_LOCATOR_KEYS
        )
        if locator_keys:
            raise ConfigError("local_model_locator_forbidden", safe_name=locator_keys[0][:256])
        allowed = frozenset(
            {
                "profile_id",
                "profile_version",
                "endpoint_profile_id",
                "endpoint_profile_version",
                "model",
                "protocol_version",
                "judgment_schema_version",
                "capability_digest",
                "timeout_seconds",
            }
        )
        _reject_unknown(value, allowed)
        source = _mapping(value)
        for key in allowed - {"capability_digest", "timeout_seconds"}:
            if key in source:
                _validate_identifier(source[key])
        if "capability_digest" in source and (
            type(source["capability_digest"]) is not str
            or _DIGEST.fullmatch(source["capability_digest"]) is None
        ):
            raise ConfigError("config_value_invalid")
        return value


class NetworkPolicy(str, Enum):  # noqa: UP042 - frozen composition vocabulary
    DENIED = "denied"
    CANDIDATE_EXTERNAL = "candidate_external"
    EXPLICIT_PER_PROBE = "explicit_per_probe"


class SemanticPolicy(str, Enum):  # noqa: UP042 - frozen composition vocabulary
    OPTIONAL_LOCAL_MODEL = "optional_local_model"
    OPTIONAL_EXTERNAL = "optional_external"
    SCRIPTED_FAKE = "scripted_fake"
    NO_IMPLICIT_MODEL = "no_implicit_model"


@dataclass(frozen=True, slots=True)
class ProfileCapabilities:
    network: NetworkPolicy
    semantic: SemanticPolicy

    def __post_init__(self) -> None:
        if type(self.network) is not NetworkPolicy or type(self.semantic) is not SemanticPolicy:
            raise TypeError("profile_capability_wrong_type")


PROFILE_CAPABILITIES: Final[Mapping[str, ProfileCapabilities]] = MappingProxyType(
    {
        "strict-local": ProfileCapabilities(
            NetworkPolicy.DENIED, SemanticPolicy.OPTIONAL_LOCAL_MODEL
        ),
        "local-openai": ProfileCapabilities(
            NetworkPolicy.CANDIDATE_EXTERNAL, SemanticPolicy.OPTIONAL_EXTERNAL
        ),
        "test-fake": ProfileCapabilities(NetworkPolicy.DENIED, SemanticPolicy.SCRIPTED_FAKE),
        "release-probe": ProfileCapabilities(
            NetworkPolicy.EXPLICIT_PER_PROBE, SemanticPolicy.NO_IMPLICIT_MODEL
        ),
    }
)


# Imported only after ConfigError and the shared strict base exist; config/privacy.py uses both.
from yoetz.config.privacy import PrivacyBootstrapConfig  # noqa: E402


class YoetzConfig(StrictConfigModel):
    schema_version: Literal["1"] = "1"
    profile: Literal["strict-local", "local-openai", "test-fake", "release-probe"] = "strict-local"
    storage: StorageConfig = Field(default_factory=StorageConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    observation: ObservationConfig = Field(default_factory=ObservationConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    privacy: PrivacyBootstrapConfig = Field(default_factory=PrivacyBootstrapConfig)
    provider: ProviderProfileConfig | None = None
    local_model: LocalModelProfileConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def _validate_raw(cls, value: object) -> object:
        allowed = frozenset(
            {
                "schema_version",
                "profile",
                "storage",
                "verification",
                "observation",
                "logging",
                "privacy",
                "provider",
                "local_model",
            }
        )
        _reject_unknown(value, allowed)
        source = _mapping(value)
        if "schema_version" in source and source["schema_version"] != "1":
            raise ConfigError("config_schema_unsupported")
        for section in ("provider", "local_model", "privacy"):
            raw_section = source.get(section)
            if isinstance(raw_section, Mapping):
                _scan_secret_keys(cast(Mapping[object, object], raw_section))
        return value

    @model_validator(mode="after")
    def _validate_profile(self) -> YoetzConfig:
        if self.profile == "strict-local" and self.provider is not None:
            raise ConfigError("strict_local_forbids_provider")
        if self.profile == "local-openai" and self.local_model is not None:
            raise ConfigError("external_profile_forbids_local_model")
        if self.profile == "test-fake" and self.provider is not None:
            raise ConfigError("test_fake_forbids_provider")
        if self.profile == "test-fake" and self.local_model is not None:
            raise ConfigError("test_fake_forbids_local_model")
        if (
            self.profile == "local-openai"
            and self.verification.semantic != "disabled"
            and self.provider is None
        ):
            raise ConfigError("provider_required_for_semantic")
        return self
