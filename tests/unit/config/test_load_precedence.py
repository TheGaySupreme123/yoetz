from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from yoetz.config.load import load_config, parse_minimal_safe_config
from yoetz.config.models import ConfigError


class _NoValueReads(Mapping[str, str]):
    def __init__(self, keys: tuple[str, ...]) -> None:
        self._keys = keys
        self.reads = 0

    def __getitem__(self, key: str) -> str:
        self.reads += 1
        raise AssertionError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


def test_leaf_precedence_and_selected_scalar_parsing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
profile = "test-fake"
[storage]
data_dir = "/file/data"
durability = "full"
[verification]
semantic = "optional"
max_findings = 4
[observation]
enabled = false
[logging]
level = "warning"
payloads = false
"""
    )
    config = load_config(
        {
            "verification.max_findings": "6",
            "logging.level": "debug",
        },
        {
            "YOETZ_VERIFICATION_MAX_FINDINGS": "5",
            "YOETZ_LOG_LEVEL": "error",
            "YOETZ_STORAGE_DATA_DIR": "/env/data",
        },
        config_path,
    )
    assert config.profile == "test-fake"
    assert config.verification.max_findings == 6
    assert config.observation.enabled is False
    assert config.logging.level == "debug"
    assert config.logging.payloads is False
    assert config.storage.data_dir == Path("/env/data")


def test_shadowed_lower_precedence_scalar_is_not_parsed(tmp_path: Path) -> None:
    config = load_config(
        {"verification.max_findings": "7"},
        {"YOETZ_VERIFICATION_MAX_FINDINGS": "not-an-integer"},
        tmp_path / "missing.toml",
    )
    assert config.verification.max_findings == 7


def test_secret_name_precedes_unknown_without_value_reads() -> None:
    env = _NoValueReads(("YOETZ_Z_UNKNOWN", "YOETZ_PROVIDER_API_KEY"))
    with pytest.raises(ConfigError) as caught:
        load_config({}, env, None)
    assert caught.value.reason_code == "secret_env_forbidden"
    assert caught.value.safe_name == "YOETZ_PROVIDER_API_KEY"
    assert env.reads == 0

    unknown = _NoValueReads(("YOETZ_Z_UNKNOWN",))
    with pytest.raises(ConfigError) as caught_unknown:
        load_config({}, unknown, None)
    assert caught_unknown.value.reason_code == "unknown_config_env_var"
    assert unknown.reads == 0


def test_unknown_and_secret_service_override_names_fail_closed() -> None:
    with pytest.raises(ConfigError) as unknown:
        load_config({"provider.unknown": "x"}, {}, None)
    assert unknown.value.reason_code == "unknown_config_override"
    with pytest.raises(ConfigError) as secret:
        load_config({"provider.api_key": "never"}, {}, None)
    assert secret.value.reason_code == "secret_config_override_forbidden"
    assert "never" not in repr(secret.value)


def test_observation_has_no_environment_or_service_override_surface() -> None:
    with pytest.raises(ConfigError) as environment:
        load_config({}, {"YOETZ_OBSERVATION_ENABLED": "false"}, None)
    assert environment.value.reason_code == "unknown_config_env_var"
    assert environment.value.safe_name == "YOETZ_OBSERVATION_ENABLED"

    with pytest.raises(ConfigError) as service_override:
        load_config({"observation.enabled": "false"}, {}, None)
    assert service_override.value.reason_code == "unknown_config_override"
    assert service_override.value.safe_name == "observation.enabled"


def test_toml_parser_size_and_release_probe_rules(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.toml"
    duplicate.write_text('profile="strict-local"\nprofile="test-fake"\n')
    with pytest.raises(ConfigError) as invalid:
        load_config({}, {}, duplicate)
    assert invalid.value.reason_code == "config_toml_invalid"
    assert invalid.value.line == 2

    oversized = tmp_path / "oversized.toml"
    oversized.write_bytes(b"#" * 65_537)
    with pytest.raises(ConfigError) as too_large:
        load_config({}, {}, oversized)
    assert too_large.value.reason_code == "config_file_too_large"

    probe = tmp_path / "probe.toml"
    probe.write_text('profile="release-probe"\n')
    with pytest.raises(ConfigError) as release_probe:
        load_config({}, {"YOETZ_PROFILE": "strict-local"}, probe)
    assert release_probe.value.reason_code == "release_probe_not_a_user_profile"


def test_missing_file_uses_defaults_and_empty_env_is_unset(tmp_path: Path) -> None:
    config = load_config({}, {"YOETZ_LOG_LEVEL": ""}, tmp_path / "missing.toml")
    assert config.profile == "strict-local"
    assert config.logging.level == "info"


def test_tui_opt_out_is_known_process_control_not_a_config_override(tmp_path: Path) -> None:
    config = load_config({}, {"YOETZ_TUI": "0"}, tmp_path / "missing.toml")

    assert config.profile == "strict-local"


def test_minimal_parse_ignores_nonminimal_provider_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "minimal.toml"
    config_path.write_text(
        """
profile = "strict-local"
[storage]
data_dir = "/chosen/data"
[logging]
level = "warning"
[provider]
timeout_seconds = "broken-for-full-validation"
unreviewed = [1, 2, 3]
"""
    )
    minimal = parse_minimal_safe_config({}, {"config": str(config_path)})
    assert minimal.profile == "strict-local"
    assert minimal.data_dir == Path("/chosen/data")
    assert minimal.log_level == "warning"
    assert minimal.config_path_used == config_path

    with pytest.raises(ConfigError):
        load_config({}, {}, config_path)


def test_explicit_project_config_diagnostic_is_bounded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".git").mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text('profile="test-fake"\n')
    assert load_config({}, {}, config_path).profile == "test-fake"
    diagnostic = capsys.readouterr().err
    assert diagnostic == '{"reason":"explicit_project_config"}\n'
    assert str(tmp_path) not in diagnostic
