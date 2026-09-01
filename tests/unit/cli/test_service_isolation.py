"""Connection-free isolation proof for the dogfood preflight (issue #518).

The report must prove — with digests over canonical resolved path identities, never raw paths —
which identity roots this exact environment would use, so a parity preflight can reject shared,
ambient, or unprovable Yoetz service/state identity before any launch.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from yoetz.cli.isolation_status import isolation_report
from yoetz.config.paths import ISOLATED_ROOT_ENV, PathSafetyError

_DIGEST_KEYS = ("state_digest", "endpoint_digest", "storage_digest", "config_digest")


@pytest.fixture
def private_root() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix=".yz-isolation-test-", dir=Path.home()))
    root.chmod(0o700)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _scrub_yoetz_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in [name for name in os.environ if name.startswith("YOETZ_")]:
        monkeypatch.delenv(name, raising=False)


def _expected_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(str(path.resolve(strict=False)).encode("utf-8")).hexdigest()


def test_ambient_mode_reports_the_exact_platform_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scrub_yoetz_env(monkeypatch)
    # A hermetic explicit config keeps the probe away from the live user config file.
    monkeypatch.setenv("YOETZ_CONFIG", str(tmp_path / "missing.toml"))

    report = isolation_report()

    assert report["mode"] == "ambient"
    identity = cast(dict[str, str], report["identity"])
    assert set(identity) == {*_DIGEST_KEYS, "executable_digest"}


def test_isolated_mode_reports_every_identity_beneath_the_exact_root(
    private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scrub_yoetz_env(monkeypatch)
    root = private_root / "iso"
    root.mkdir(mode=0o700)
    monkeypatch.setenv(ISOLATED_ROOT_ENV, str(root))

    report = isolation_report()

    assert report["mode"] == "isolated"
    assert report["identity"]["state_digest"] == _expected_digest(root / "state")
    assert report["identity"]["endpoint_digest"] == _expected_digest(root / "run")
    assert report["identity"]["storage_digest"] == _expected_digest(root / "data")
    assert report["identity"]["config_digest"] == _expected_digest(root / "config" / "config.toml")
    # Digest-only privacy boundary: no raw path may appear anywhere in the report.
    assert str(root) not in repr(report)


def test_two_target_reports_expose_shared_relocated_storage(
    private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact normal target, not platform defaults, supplies the comparison identity."""

    _scrub_yoetz_env(monkeypatch)
    relocated = private_root / "relocated-data"
    relocated.mkdir(mode=0o700)
    monkeypatch.setenv("YOETZ_STORAGE_DATA_DIR", str(relocated))

    normal = isolation_report()

    root = private_root / "iso"
    root.mkdir(mode=0o700)
    monkeypatch.setenv(ISOLATED_ROOT_ENV, str(root))
    isolated = isolation_report()

    assert normal["mode"] == "ambient"
    assert isolated["mode"] == "isolated"
    assert isolated["identity"]["storage_digest"] == normal["identity"]["storage_digest"]


def test_unusable_root_is_unprovable_never_ambient(monkeypatch: pytest.MonkeyPatch) -> None:
    _scrub_yoetz_env(monkeypatch)
    monkeypatch.setenv(ISOLATED_ROOT_ENV, "relative/never-valid")

    with pytest.raises(PathSafetyError) as caught:
        isolation_report()
    assert caught.value.reason_code == "isolation_root_invalid"
