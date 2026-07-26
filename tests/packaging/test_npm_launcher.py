"""npm launcher: publish-ready shape, delegation-only behavior, unpublished guarantee."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_LAUNCHER_DIR = Path(__file__).resolve().parents[2] / "support" / "npm-launcher"


def _package() -> dict[str, object]:
    return json.loads((_LAUNCHER_DIR / "package.json").read_text(encoding="utf-8"))


def test_package_shape_is_delegation_only() -> None:
    package = _package()
    assert package["name"] == "yoetz"
    # The load-bearing "not published yet" guarantee: npm publish refuses it.
    assert package["private"] is True
    assert package["bin"] == {"yoetz": "./bin/yoetz.js"}
    # No runtime dependencies: the launcher may never bundle or fetch code itself.
    for forbidden in ("dependencies", "devDependencies", "optionalDependencies", "scripts"):
        assert forbidden not in package, forbidden
    assert package["license"] == "Apache-2.0"


def test_version_stays_in_lockstep_with_the_python_distribution() -> None:
    import yoetz

    assert _package()["version"] == yoetz.__version__


def test_launcher_script_exists_and_is_executable() -> None:
    script = _LAUNCHER_DIR / "bin" / "yoetz.js"
    assert script.is_file()
    assert os.access(script, os.X_OK)
    first_line = script.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == "#!/usr/bin/env node"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is a contributor-only tool")
def test_launcher_delegates_to_pinned_uvx(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node is not None
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    record = tmp_path / "record.txt"
    (shim_dir / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (shim_dir / "uvx").write_text(f'#!/bin/sh\necho "$@" > {record}\nexit 7\n', encoding="utf-8")
    os.chmod(shim_dir / "uv", 0o755)
    os.chmod(shim_dir / "uvx", 0o755)

    completed = subprocess.run(
        (node, str(_LAUNCHER_DIR / "bin" / "yoetz.js"), "status", "--json"),
        env={**os.environ, "PATH": str(shim_dir)},
        capture_output=True,
        timeout=30,
        check=False,
    )
    # Exact version-pinned passthrough and child exit-code propagation.
    assert completed.returncode == 7
    version = _package()["version"]
    assert record.read_text(encoding="utf-8").strip() == f"yoetz=={version} status --json"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is a contributor-only tool")
def test_launcher_fails_with_guidance_when_uv_is_absent(tmp_path: Path) -> None:
    node = shutil.which("node")
    assert node is not None
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    completed = subprocess.run(
        (node, str(_LAUNCHER_DIR / "bin" / "yoetz.js")),
        env={**os.environ, "PATH": str(empty_dir)},
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 1
    stderr = completed.stderr.decode("utf-8")
    assert "uv" in stderr
    assert "astral.sh/uv" in stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is a contributor-only tool")
def test_launcher_reports_signal_termination_as_the_conventional_exit_code(
    tmp_path: Path,
) -> None:
    """A script that checks exit codes must see the same value either entry point gives.

    `npx yoetz` has to be interchangeable with the Python console script, and an
    interrupted interactive session exits 130 there.
    """

    node = shutil.which("node")
    assert node is not None
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    (shim_dir / "uv").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    # Kill the child with SIGINT, exactly as Ctrl-C at a terminal would.
    (shim_dir / "uvx").write_text("#!/bin/sh\nkill -INT $$\n", encoding="utf-8")
    os.chmod(shim_dir / "uv", 0o755)
    os.chmod(shim_dir / "uvx", 0o755)

    completed = subprocess.run(
        (node, str(_LAUNCHER_DIR / "bin" / "yoetz.js")),
        env={**os.environ, "PATH": str(shim_dir)},
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 130


@pytest.mark.skipif(shutil.which("node") is None, reason="node is a contributor-only tool")
def test_launcher_guidance_names_the_runtime_and_a_concrete_install_command(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    assert node is not None
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    completed = subprocess.run(
        (node, str(_LAUNCHER_DIR / "bin" / "yoetz.js")),
        env={**os.environ, "PATH": str(empty_dir)},
        capture_output=True,
        timeout=30,
        check=False,
    )
    stderr = completed.stderr.decode("utf-8")
    assert "Python 3.14" in stderr
    assert "astral.sh/uv/install.sh" in stderr
    assert "never installs anything itself" in stderr


def test_launcher_never_reimplements_setup_or_interface_logic() -> None:
    """The launcher stays a delegator: no TTY probing, no setup, no interface."""

    source = (_LAUNCHER_DIR / "bin" / "yoetz.js").read_text(encoding="utf-8")
    for forbidden in ("isTTY", "readline", "prompt(", "createInterface", "pip install"):
        assert forbidden not in source, forbidden
    # stdio must be inherited so the child sees the real controlling terminal.
    assert 'stdio: "inherit"' in source
