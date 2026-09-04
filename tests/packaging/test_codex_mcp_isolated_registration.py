"""Installed Codex 0.150.1 propagation regression for issue #561.

The test uses a fresh Codex home and a test-owned executable named ``yoetz``.  Product code
registers the exact allowlisted isolation binding, then the real Codex app-server launches that
registered child while its own parent environment deliberately lacks ``YOETZ_ISOLATED_ROOT``.
The child records the value it actually received and completes the MCP handshake.  This proves
the reviewed registration, not ambient parent inheritance, supplied the exact isolated root.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final, cast

import anyio
import pytest

from yoetz.adapters.integrations.codex_mcp import CodexMcpAdapter
from yoetz.application.harness_mcp import HarnessMcpService, McpRegistrationConfirmation
from yoetz.config.paths import ISOLATED_ROOT_ENV
from yoetz.ports.harness_mcp import HarnessBinary, McpRegistrationState
from yoetz.ports.integrations import HarnessId

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_EXPECTED_CODEX_VERSION: Final = "codex-cli 0.150.1"
_PROBE_MARKER: Final = "codex-child-isolated-root.txt"
_PROBE_SCRIPT: Final = r"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

root = os.environ.get("YOETZ_ISOLATED_ROOT")
if not root:
    raise SystemExit(73)
pathlib.Path(root, "codex-child-isolated-root.txt").write_text(root, encoding="utf-8")

for raw in sys.stdin.buffer:
    try:
        request = json.loads(raw)
    except Exception:
        continue
    request_id = request.get("id")
    if request_id is None:
        continue
    method = request.get("method")
    if method == "initialize":
        params = request.get("params") or {}
        result = {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "yoetz-isolation-probe", "version": "0.1.0"},
        }
    elif method == "tools/list":
        result = {"tools": []}
    elif method == "resources/list":
        result = {"resources": []}
    elif method == "resources/templates/list":
        result = {"resourceTemplates": []}
    elif method == "prompts/list":
        result = {"prompts": []}
    elif method == "ping":
        result = {}
    else:
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "method not found"},
        }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        continue
    response = {"jsonrpc": "2.0", "id": request_id, "result": result}
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    sys.stdout.flush()
"""


def _is_advertised_host() -> bool:
    return sys.platform == "darwin" and platform.machine() == "arm64"


pytestmark = pytest.mark.skipif(
    not _is_advertised_host(),
    reason="the issue #561 host regression is the installed macOS arm64 Codex 0.150.1 cell",
)


def _installed_codex_01501() -> Path:
    candidate = shutil.which("codex")
    if candidate is None:
        pytest.skip("installed Codex 0.150.1 is unavailable")
    executable = Path(candidate).resolve(strict=True)
    result = subprocess.run(
        [str(executable), "--version"],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0 or result.stdout.decode("utf-8").strip() != _EXPECTED_CODEX_VERSION:
        pytest.skip("installed Codex is not the frozen 0.150.1 regression cell")
    return executable


def _short_private_root() -> Path:
    base = Path.home() / ".yz-mcp561"
    base.mkdir(mode=0o700, exist_ok=True)
    base.chmod(0o700)
    root = base / secrets.token_hex(4)
    root.mkdir(mode=0o700)
    return root


def test_installed_codex_child_receives_only_the_reviewed_isolated_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codex = _installed_codex_01501()
    root = _short_private_root()
    try:
        isolated_root = root / "isolated"
        codex_home = root / "codex-home"
        probe_bin = root / "bin"
        isolated_root.mkdir(mode=0o700)
        codex_home.mkdir(mode=0o700)
        probe_bin.mkdir(mode=0o700)
        probe = probe_bin / "yoetz"
        probe.write_text(_PROBE_SCRIPT, encoding="utf-8")
        probe.chmod(0o700)

        original_path = os.environ.get("PATH", "")
        monkeypatch.setenv("PATH", f"{probe_bin}{os.pathsep}{original_path}")
        monkeypatch.setenv("CODEX_HOME", str(codex_home))
        monkeypatch.setenv("CODEX_TESTING_HOME", str(codex_home))
        monkeypatch.setenv(ISOLATED_ROOT_ENV, str(isolated_root))

        binary = HarnessBinary(HarnessId.CODEX, str(codex), "0.150.1", "supported")
        service = HarnessMcpService(CodexMcpAdapter(route_profile="strict"))
        preview = anyio.run(lambda: service.preview(binary))
        assert preview.isolated_root == str(isolated_root)
        result = anyio.run(
            lambda: service.register(
                binary,
                McpRegistrationConfirmation(
                    preview.preview_digest,
                    True,
                    "noninteractive_flag",
                ),
                _state=root / "applied-route",
            )
        )
        assert result.state_after is McpRegistrationState.YOETZ_OWNED

        observed = anyio.run(lambda: service.observe(binary))
        assert observed.isolation_binding == "isolated_exact"

        # The host parent has no isolation variable. Only the reviewed Codex registration can
        # supply the value to the child that the real app-server now launches.
        monkeypatch.delenv(ISOLATED_ROOT_ENV)
        capture = root / "mcp-server-status.json"
        launched = subprocess.run(
            [
                sys.executable,
                str(_REPO_ROOT / "scripts" / "capture_codex_mcp_surface.py"),
                "--codex-binary",
                str(codex),
                "--codex-testing-home",
                str(codex_home),
                "--output",
                str(capture),
            ],
            capture_output=True,
            timeout=45,
            env=os.environ.copy(),
            check=False,
        )
        assert launched.returncode == 0, launched.stderr.decode("utf-8", errors="replace")
        document = cast(dict[str, object], json.loads(capture.read_bytes()))
        inventory = cast(dict[str, object], document["inventory"])
        result_body = cast(dict[str, object], inventory["result"])
        entries = cast(list[dict[str, object]], result_body["data"])
        assert entries[0]["serverInfo"] == {
            "description": None,
            "icons": None,
            "name": "yoetz-isolation-probe",
            "title": None,
            "version": "0.1.0",
            "websiteUrl": None,
        }
        assert (isolated_root / _PROBE_MARKER).read_text(encoding="utf-8") == str(isolated_root)

        monkeypatch.setenv(ISOLATED_ROOT_ENV, str(isolated_root))
        removal = anyio.run(lambda: service.preview_unregistration(binary))
        removed = anyio.run(
            lambda: service.unregister(
                binary,
                McpRegistrationConfirmation(
                    removal.preview_digest,
                    True,
                    "noninteractive_flag",
                ),
                _state=root / "applied-route",
            )
        )
        assert removed.state_after is McpRegistrationState.ABSENT
    finally:
        shutil.rmtree(root, ignore_errors=True)
        try:
            root.parent.rmdir()
        except OSError:
            pass
