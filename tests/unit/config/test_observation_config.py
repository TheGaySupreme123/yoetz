from __future__ import annotations

import tomllib
from pathlib import Path
from types import TracebackType

import pytest

from yoetz.config.load import load_config
from yoetz.config.models import ConfigError, ObservationConfig, YoetzConfig
from yoetz.config.write import (
    render_config_toml,
    write_config_toml,
    write_config_toml_if_unchanged,
)


def test_observation_config_toml_round_trip(tmp_path: Path) -> None:
    config = YoetzConfig(observation=ObservationConfig(enabled=False))

    rendered = render_config_toml(config)
    assert "[observation]\nenabled = false\n" in rendered
    assert YoetzConfig.model_validate(tomllib.loads(rendered), strict=True) == config

    path = write_config_toml(config, path=tmp_path / "config.toml")
    assert load_config({}, {}, path) == config


def test_config_compare_and_swap_accepts_exact_preimage(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    disabled = YoetzConfig(observation=ObservationConfig(enabled=False))
    enabled = YoetzConfig(observation=ObservationConfig(enabled=True))
    write_config_toml(disabled, path=path)
    expected = path.read_bytes()

    assert write_config_toml_if_unchanged(enabled, expected_bytes=expected, path=path) == path
    assert load_config({}, {}, path) == enabled
    assert (tmp_path / ".config.toml.lock").stat().st_mode & 0o777 == 0o600


def test_config_compare_and_swap_rejects_stale_preimage_without_writing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    disabled = YoetzConfig(observation=ObservationConfig(enabled=False))
    enabled = YoetzConfig(observation=ObservationConfig(enabled=True))
    write_config_toml(disabled, path=path)
    stale = path.read_bytes()
    concurrent = stale + b"\n# concurrent owner edit\n"
    path.write_bytes(concurrent)

    with pytest.raises(ConfigError) as caught:
        write_config_toml_if_unchanged(enabled, expected_bytes=stale, path=path)

    assert caught.value.reason_code == "config_preimage_mismatch"
    assert path.read_bytes() == concurrent


def test_config_compare_and_swap_distinguishes_absent_from_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    config = YoetzConfig()

    path.write_bytes(b"")
    with pytest.raises(ConfigError) as caught:
        write_config_toml_if_unchanged(config, expected_bytes=None, path=path)
    assert caught.value.reason_code == "config_preimage_mismatch"
    assert path.read_bytes() == b""

    path.unlink()
    write_config_toml_if_unchanged(config, expected_bytes=None, path=path)
    assert load_config({}, {}, path) == config


def test_all_config_writers_share_the_interprocess_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered: list[Path] = []

    class RecordingLock:
        def __init__(self, target: Path) -> None:
            self.target = target

        def __enter__(self) -> None:
            entered.append(self.target)

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            del exc_type, exc, traceback

    monkeypatch.setattr("yoetz.config.write._ConfigWriteLock", RecordingLock)
    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"

    write_config_toml(YoetzConfig(), path=first)
    write_config_toml_if_unchanged(YoetzConfig(), expected_bytes=None, path=second)

    assert entered == [first, second]


def test_config_writer_refuses_preplanted_lock_symlink(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    victim = tmp_path / "victim"
    victim.write_bytes(b"owner content")
    (tmp_path / ".config.toml.lock").symlink_to(victim)

    with pytest.raises(ConfigError) as caught:
        write_config_toml(YoetzConfig(), path=path)

    assert caught.value.reason_code == "config_value_invalid"
    assert victim.read_bytes() == b"owner content"
    assert not path.exists()
