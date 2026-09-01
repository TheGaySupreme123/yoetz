"""Connection-free isolation proof for the dogfood preflight (issue #518).

The report must prove — with digests over canonical resolved path identities, never raw paths —
which identity roots this exact environment would use, so a parity preflight can reject shared,
ambient, or unprovable Yoetz service/state identity before any launch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest

from yoetz.cli.isolation_status import isolation_report
from yoetz.config.paths import ISOLATED_ROOT_ENV, PathSafetyError

_DIGEST_KEYS = ("state_digest", "endpoint_digest", "storage_digest", "config_digest")


def _scrub_yoetz_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in [name for name in os.environ if name.startswith("YOETZ_")]:
        monkeypatch.delenv(name, raising=False)


def _expected_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(str(path.resolve(strict=False)).encode("utf-8")).hexdigest()


def test_ambient_mode_reports_identical_platform_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scrub_yoetz_env(monkeypatch)
    # A hermetic explicit config keeps the probe away from the live user config file.
    monkeypatch.setenv("YOETZ_CONFIG", str(tmp_path / "missing.toml"))

    report = isolation_report()

    assert report["mode"] == "ambient"
    assert report["distinct"] is False
    identity = cast(dict[str, str], report["identity"])
    ambient = cast(dict[str, str], report["ambient_identity"])
    for key in ("state_digest", "endpoint_digest", "storage_digest"):
        assert identity[key] == ambient[key]
    assert set(identity) == {*_DIGEST_KEYS, "executable_digest"}
    assert set(ambient) == set(_DIGEST_KEYS)


def test_isolated_mode_proves_every_identity_root_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scrub_yoetz_env(monkeypatch)
    root = tmp_path / "iso"
    root.mkdir(mode=0o700)
    monkeypatch.setenv(ISOLATED_ROOT_ENV, str(root))

    report = isolation_report()

    assert report["mode"] == "isolated"
    assert report["distinct"] is True
    identity = cast(dict[str, str], report["identity"])
    ambient = cast(dict[str, str], report["ambient_identity"])
    for key in _DIGEST_KEYS:
        assert identity[key] != ambient[key]
    assert report["identity"]["state_digest"] == _expected_digest(root / "state")
    assert report["identity"]["endpoint_digest"] == _expected_digest(root / "run")
    assert report["identity"]["storage_digest"] == _expected_digest(root / "data")
    assert report["identity"]["config_digest"] == _expected_digest(root / "config" / "config.toml")
    # Digest-only privacy boundary: no raw path may appear anywhere in the report.
    assert str(root) not in repr(report)


def test_isolated_storage_pointed_at_ambient_data_is_not_distinct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """YOETZ_STORAGE_DATA_DIR alone is not isolation; the report must expose the overlap."""

    from platformdirs import PlatformDirs

    _scrub_yoetz_env(monkeypatch)
    root = tmp_path / "iso"
    root.mkdir(mode=0o700)
    monkeypatch.setenv(ISOLATED_ROOT_ENV, str(root))
    ambient_data = PlatformDirs(appname="yoetz", appauthor=False, roaming=False).user_data_dir
    monkeypatch.setenv("YOETZ_STORAGE_DATA_DIR", str(ambient_data))

    report = isolation_report()

    assert report["mode"] == "isolated"
    assert report["distinct"] is False
    assert report["identity"]["storage_digest"] == report["ambient_identity"]["storage_digest"]


def test_unusable_root_is_unprovable_never_ambient(monkeypatch: pytest.MonkeyPatch) -> None:
    _scrub_yoetz_env(monkeypatch)
    monkeypatch.setenv(ISOLATED_ROOT_ENV, "relative/never-valid")

    with pytest.raises(PathSafetyError) as caught:
        isolation_report()
    assert caught.value.reason_code == "isolation_root_invalid"
