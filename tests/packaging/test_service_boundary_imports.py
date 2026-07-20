"""Installed trust-boundary import suite (ADR-008, ADR-011).

Proves, against the *installed* wheel in a clean isolated interpreter, that the ordinary CLI and
MCP import graphs can never reach trusted vault/storage/provider/privacy-gateway/application
composition; that only ``yoetz.service.daemon`` imports that ready composition; that
``HumanControlClient``/``ConfidentialSecretClient`` are reachable only from the trusted CLI unlock/
privacy helper modules; and that the ADR-011 ``state capture`` command path lazily imports only
``ports.subject_state`` and ``adapters.git_subject_state``, never trusted composition. See
``specs/tests/packaging/test_service_boundary_imports.py.md``.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_BUILD_TIMEOUT: Final = 120
_PROBE_TIMEOUT: Final = 30

# Trusted composition/storage/vault/provider/privacy-gateway modules that must never be reachable
# from an ordinary client (CLI) or MCP import graph.
_FORBIDDEN_PREFIXES: Final = (
    "yoetz.adapters.keys.",
    "yoetz.adapters.sqlite.",
    "yoetz.adapters.objects.",
    "yoetz.adapters.privacy.",
    "yoetz.adapters.providers.",
    "yoetz.adapters.memory.",
    "yoetz.adapters.session_events",
    "yoetz.adapters.runtime",
    "yoetz.application.",
    "yoetz.service.daemon",
    "yoetz.service.vault",
    "yoetz.service.confidential_client",
    "yoetz.service.confidential_protocol",
    "yoetz.service.human_control",
    "yoetz.service.unlock",
    "yoetz.service.secret_ingress",
    "yoetz.service.lifecycle",
    "yoetz.cli.unlock",
    "yoetz.cli.privacy_control",
)


def _forbidden_hits(modules: list[str]) -> list[str]:
    return [module for module in modules if module.startswith(_FORBIDDEN_PREFIXES)]


@dataclass(frozen=True, slots=True)
class InstalledCandidate:
    python: Path


@pytest.fixture(scope="module")
def installed_candidate(tmp_path_factory: pytest.TempPathFactory) -> InstalledCandidate:
    build_out = tmp_path_factory.mktemp("service-boundary-build")
    environment = dict(os.environ)
    environment["TZ"] = "UTC"
    environment["SOURCE_DATE_EPOCH"] = "1700000000"
    subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local uv binary
        [
            "uv",
            "build",
            "--wheel",
            "--no-sources",
            "--offline",
            "--no-create-gitignore",
            "-o",
            str(build_out),
        ],
        cwd=_REPO_ROOT,
        env=environment,
        capture_output=True,
        check=True,
        timeout=_BUILD_TIMEOUT,
    )
    wheels = [entry for entry in build_out.iterdir() if entry.name.endswith(".whl")]
    assert len(wheels) == 1

    venv_dir = tmp_path_factory.mktemp("service-boundary-venv") / "venv"
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["uv", "venv", "--python", "3.14", str(venv_dir)],
        capture_output=True,
        check=True,
        timeout=_BUILD_TIMEOUT,
    )
    python = venv_dir / "bin" / "python"
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["uv", "pip", "install", "--python", str(python), "--offline", str(wheels[0])],
        capture_output=True,
        check=True,
        timeout=_BUILD_TIMEOUT,
    )
    return InstalledCandidate(python=python)


def _imported_modules(installed: InstalledCandidate, import_statement: str) -> list[str]:
    """Import ``import_statement`` in a clean interpreter and return the resulting ``yoetz.*``
    module graph, sorted.
    """

    script = (
        f"{import_statement}\n"
        "import sys, json\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.startswith('yoetz.') or m == 'yoetz')))"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local interpreter
        [str(installed.python), "-c", script],
        capture_output=True,
        check=True,
        timeout=_PROBE_TIMEOUT,
    )
    return json.loads(completed.stdout)


# --------------------------------------------------------------------------
# Ordinary CLI and MCP import graphs
# --------------------------------------------------------------------------


def test_cli_app_import_graph_excludes_all_trusted_composition(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.cli.app")
    hits = _forbidden_hits(modules)
    assert not hits, f"yoetz.cli.app imports trusted composition: {hits}"


def test_mcp_server_import_graph_excludes_all_trusted_composition(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.mcp.server")
    hits = _forbidden_hits(modules)
    assert not hits, f"yoetz.mcp.server imports trusted composition: {hits}"


def test_mcp_server_import_graph_excludes_the_cli_and_its_trusted_helpers(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.mcp.server")
    cli_modules = [module for module in modules if module.startswith("yoetz.cli")]
    assert not cli_modules, f"MCP unexpectedly reaches CLI modules: {cli_modules}"


def test_cli_app_import_graph_uses_only_the_ordinary_service_client(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.cli.app")
    service_modules = {module for module in modules if module.startswith("yoetz.service.")}
    assert service_modules == {"yoetz.service.client", "yoetz.service.control_protocol"}


def test_mcp_server_import_graph_uses_only_the_ordinary_service_client(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.mcp.server")
    service_modules = {module for module in modules if module.startswith("yoetz.service.")}
    assert service_modules == {"yoetz.service.client", "yoetz.service.control_protocol"}


# --------------------------------------------------------------------------
# The daemon is the sole ready-composition owner
# --------------------------------------------------------------------------


def test_daemon_import_graph_is_the_ready_composition_owner(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.service.daemon")
    required_present = (
        "yoetz.adapters.keys.encrypted_vault",
        "yoetz.adapters.keys.os_keyring",
        "yoetz.application.service",
        "yoetz.service.daemon",
        "yoetz.service.vault",
        "yoetz.service.human_control",
        "yoetz.service.unlock",
        "yoetz.service.secret_ingress",
    )
    for module in required_present:
        assert module in modules, f"daemon does not compose {module}"


def test_only_the_daemon_module_reaches_the_ready_composition(
    installed_candidate: InstalledCandidate,
) -> None:
    cli_modules = _imported_modules(installed_candidate, "import yoetz.cli.app")
    mcp_modules = _imported_modules(installed_candidate, "import yoetz.mcp.server")
    assert "yoetz.service.daemon" not in cli_modules
    assert "yoetz.service.daemon" not in mcp_modules


# --------------------------------------------------------------------------
# HumanControlClient / ConfidentialSecretClient: trusted CLI helpers only
# --------------------------------------------------------------------------


def test_confidential_client_is_absent_from_the_plain_cli_app_import(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.cli.app")
    assert "yoetz.service.confidential_client" not in modules


def test_confidential_client_is_reachable_from_the_trusted_cli_unlock_helper(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.cli.unlock")
    assert "yoetz.service.confidential_client" in modules


def test_confidential_client_is_reachable_from_the_trusted_cli_privacy_control_helper(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.cli.privacy_control")
    assert "yoetz.service.confidential_client" in modules


def test_confidential_client_is_absent_from_the_mcp_import_graph(
    installed_candidate: InstalledCandidate,
) -> None:
    modules = _imported_modules(installed_candidate, "import yoetz.mcp.server")
    assert "yoetz.service.confidential_client" not in modules
    assert "yoetz.service.human_control" not in modules


# --------------------------------------------------------------------------
# ADR-011: `state capture` is the sole ordinary CLI repository-read exception
# --------------------------------------------------------------------------


def test_state_capture_lazily_imports_only_subject_state_modules(
    installed_candidate: InstalledCandidate,
) -> None:
    script = (
        "import atexit, json, sys\n"
        "def dump():\n"
        "    mods = sorted(m for m in sys.modules if m.startswith('yoetz.') or m == 'yoetz')\n"
        "    sys.stderr.write('YOETZ_MODULES:' + json.dumps(mods) + chr(10))\n"
        "atexit.register(dump)\n"
        "from yoetz.cli.app import app\n"
        "app(sys.argv[1:], prog_name='yoetz', standalone_mode=True)\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local interpreter
        [
            str(installed_candidate.python),
            "-c",
            script,
            "state",
            "capture",
            "--workspace",
            str(_REPO_ROOT),
            "--json",
        ],
        capture_output=True,
        check=True,
        timeout=_PROBE_TIMEOUT,
    )
    marker_line = next(
        line
        for line in completed.stderr.decode("utf-8").splitlines()
        if line.startswith("YOETZ_MODULES:")
    )
    modules = json.loads(marker_line.removeprefix("YOETZ_MODULES:"))

    assert "yoetz.ports.subject_state" in modules
    assert "yoetz.adapters.git_subject_state" in modules
    hits = _forbidden_hits(modules)
    assert not hits, f"state capture imports trusted composition: {hits}"


def test_state_capture_command_performs_no_write_or_network_effect(
    installed_candidate: InstalledCandidate, tmp_path: Path
) -> None:
    """The command must not mutate the workspace it inspects (a bounded, read-only proxy for "no
    write/network effect").
    """

    before = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local git binary
        ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        check=True,
        timeout=_PROBE_TIMEOUT,
    ).stdout

    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local interpreter
        [
            str(installed_candidate.python),
            "-m",
            "yoetz",
            "state",
            "capture",
            "--workspace",
            str(_REPO_ROOT),
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=True,
        timeout=_PROBE_TIMEOUT,
    )
    assert completed.returncode == 0

    after = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted local git binary
        ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        check=True,
        timeout=_PROBE_TIMEOUT,
    ).stdout
    assert before == after
