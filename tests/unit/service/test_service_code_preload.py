"""The service loads its reachable code up front so an in-place upgrade cannot mix releases (#820)."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

import yoetz.service.daemon as daemon

_PROBE = "yz_preload_probe"


@pytest.fixture
def probe_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / _PROBE
    (root / "nested").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "lazy.py").write_text("VALUE = 1\n")
    (root / "broken.py").write_text("raise ImportError('platform-specific')\n")
    (root / "nested" / "__init__.py").write_text("")
    (root / "nested" / "deep.py").write_text("VALUE = 2\n")
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    monkeypatch.setattr(daemon, "_SERVICE_CODE_PACKAGES", (_PROBE, "yz_preload_absent"))
    monkeypatch.setattr(daemon, "_SERVICE_CODE_MODULES", (f"{_PROBE}_missing",))
    try:
        yield root
    finally:
        for name in [name for name in sys.modules if name.startswith(_PROBE)]:
            del sys.modules[name]


@pytest.mark.anyio
async def test_preload_imports_every_reachable_module_and_never_fails(
    probe_package: Path,
) -> None:
    del probe_package
    await daemon._load_service_code()
    assert f"{_PROBE}.lazy" in sys.modules
    assert f"{_PROBE}.nested.deep" in sys.modules
    # A module that cannot load here stays with its lazy caller instead of failing startup.
    assert f"{_PROBE}.broken" not in sys.modules
    assert f"{_PROBE}_missing" not in sys.modules


def test_preload_covers_the_packages_the_service_reaches() -> None:
    assert set(daemon._SERVICE_CODE_PACKAGES) >= {
        "yoetz.adapters",
        "yoetz.application",
        "yoetz.kernel",
        "yoetz.protocol",
        "yoetz.service",
    }
    # The legacy hook-spool replay runs the hook handler inside the service.
    assert "yoetz.cli.observe_hooks" in daemon._SERVICE_CODE_MODULES


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
