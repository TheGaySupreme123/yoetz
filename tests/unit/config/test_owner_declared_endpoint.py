"""Owner-declared HTTPS origin and provider TOML binding (ADR-014)."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yoetz.adapters.providers.openai_responses import (
    OpenAIProfile,
    owner_declared_data_use_profile,
)
from yoetz.cli.provider_binding import apply_provider_endpoint_choice
from yoetz.config.models import (
    OWNER_DECLARED_ENDPOINT_PROFILE_ID,
    ConfigError,
    ProviderProfileConfig,
    YoetzConfig,
    parse_https_origin,
)
from yoetz.config.write import (
    official_openai_provider,
    owner_declared_openai_provider,
    render_config_toml,
    write_provider_binding,
)

_DIGEST = "sha256:" + "a" * 64
_NOW = datetime(2026, 7, 1, tzinfo=UTC)


def test_parse_https_origin_accepts_host_and_optional_port() -> None:
    assert parse_https_origin("https://llm.example.com") == ("llm.example.com", 443)
    assert parse_https_origin("https://llm.example.com:8443") == ("llm.example.com", 8443)


@pytest.mark.parametrize(
    "value",
    [
        "http://llm.example.com",
        "https://user:pass@llm.example.com",
        "https://llm.example.com/v1",
        "https://llm.example.com?q=1",
        "https://llm.example.com#frag",
        "https://",
        "not-a-url",
        "https://host:99999",
        "https://host:abc",
        "https://host:0",
    ],
)
def test_parse_https_origin_rejects_unsafe_shapes(value: str) -> None:
    with pytest.raises(ConfigError) as caught:
        parse_https_origin(value)
    assert caught.value.reason_code == "https_origin_invalid"


def test_owner_declared_provider_requires_nested_origin() -> None:
    with pytest.raises(ConfigError) as caught:
        ProviderProfileConfig.model_validate(
            {
                "provider_id": "openai-compatible",
                "endpoint_profile_id": OWNER_DECLARED_ENDPOINT_PROFILE_ID,
                "endpoint_profile_version": "1.0.0",
                "model": "proxy-model",
                "capability_profile": "openai-responses-structured-1",
            },
            strict=True,
        )
    assert caught.value.reason_code == "owner_declared_endpoint_required"


def test_official_provider_forbids_owner_declared_section() -> None:
    with pytest.raises(ConfigError) as caught:
        ProviderProfileConfig.model_validate(
            {
                "provider_id": "openai",
                "endpoint_profile_id": "openai-responses",
                "endpoint_profile_version": "1.0.0",
                "model": "gpt-4.1-mini",
                "capability_profile": "openai-responses-structured-1",
                "owner_declared_endpoint": {"https_origin": "https://llm.example.com"},
            },
            strict=True,
        )
    assert caught.value.reason_code == "owner_declared_endpoint_forbidden"


def test_provider_rejects_free_base_url_and_secrets() -> None:
    with pytest.raises(ConfigError) as caught:
        ProviderProfileConfig.model_validate(
            {
                "provider_id": "openai",
                "endpoint_profile_id": "openai-responses",
                "endpoint_profile_version": "1.0.0",
                "model": "gpt",
                "capability_profile": "c",
                "base_url": "https://evil.example",
            },
            strict=True,
        )
    assert caught.value.reason_code == "unknown_config_key"

    with pytest.raises(ConfigError) as secret:
        ProviderProfileConfig.model_validate(
            {
                "provider_id": "openai-compatible",
                "endpoint_profile_id": OWNER_DECLARED_ENDPOINT_PROFILE_ID,
                "endpoint_profile_version": "1.0.0",
                "model": "m",
                "capability_profile": "c",
                "owner_declared_endpoint": {
                    "https_origin": "https://llm.example.com",
                    "api_key": "must-not-appear",
                },
            },
            strict=True,
        )
    assert secret.value.reason_code == "secret_in_config"
    assert "must-not-appear" not in repr(secret.value)


def test_toml_round_trip_official_and_owner_declared(tmp_path: Path) -> None:
    official = official_openai_provider(model="gpt-4.1-mini")
    path = write_provider_binding(official, path=tmp_path / "official.toml")
    loaded = YoetzConfig.model_validate(tomllib.loads(path.read_text()), strict=True)
    assert loaded.provider is not None
    assert loaded.provider.endpoint_profile_id == "openai-responses"
    assert loaded.provider.owner_declared_endpoint is None

    custom = owner_declared_openai_provider(
        model="proxy-model", https_origin="https://llm.example.com:8443"
    )
    path2 = write_provider_binding(custom, path=tmp_path / "custom.toml")
    text = path2.read_text()
    assert "https_origin" in text
    assert "api_key" not in text
    loaded2 = YoetzConfig.model_validate(tomllib.loads(text), strict=True)
    assert loaded2.provider is not None
    assert loaded2.provider.endpoint_profile_id == OWNER_DECLARED_ENDPOINT_PROFILE_ID
    assert loaded2.provider.owner_declared_endpoint is not None
    assert loaded2.provider.owner_declared_endpoint.https_origin == (
        "https://llm.example.com:8443"
    )
    assert render_config_toml(loaded2) == text


def test_apply_provider_endpoint_choice_writes_binding(tmp_path: Path) -> None:
    path, provider = apply_provider_endpoint_choice(
        "owner_declared",
        model="proxy-model",
        https_origin="https://gateway.example",
        path=tmp_path / "config.toml",
    )
    assert path.is_file()
    assert provider.endpoint_profile_id == OWNER_DECLARED_ENDPOINT_PROFILE_ID


def test_owner_declared_data_use_never_assisted_eligible() -> None:
    profile = owner_declared_data_use_profile(
        reviewed_at=_NOW,
        expires_at=_NOW + timedelta(days=30),
        evidence_digest=_DIGEST,
    )
    assert not profile.recommendation_eligible(_NOW)
    openai = OpenAIProfile(
        provider_id="openai-compatible",
        model="proxy-model",
        endpoint_profile_id=OWNER_DECLARED_ENDPOINT_PROFILE_ID,
        endpoint_profile_version="1.0.0",
        timeout_seconds=60,
        supports_structured_outputs=True,
        data_use_profile=profile,
        host="llm.example.com",
        port=8443,
    )
    assert openai.base_url == "https://llm.example.com:8443"
    assert openai.path == "/v1/responses"
