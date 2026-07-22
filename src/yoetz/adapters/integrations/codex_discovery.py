"""Read-only discovery of installed Codex CLI executables on the user PATH."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from yoetz.ports.harness_mcp import HarnessBinary
from yoetz.ports.integrations import HarnessId

__all__ = [
    "CodexProbe",
    "discover_codex_binaries",
]

_CODEX_EXECUTABLE_STEMS: Final = ("codex", "codex-testing")
_MACOS_CODEX_DIRECTORIES: Final = ("/Applications/ChatGPT.app/Contents/Resources",)
_WINDOWS_CODEX_PACKAGE_FAMILY: Final = "OpenAI.Codex_2p2nqsd0c76g0"
_WINDOWS_APP_QUERY_TIMEOUT_SECONDS: Final = 5.0
_VERSION_TIMEOUT_SECONDS: Final = 5.0
_VERSION_OUTPUT_LIMIT: Final = 4_096
_MAX_CANDIDATES: Final = 16
_VERSION_RE: Final = re.compile(r"\b(\d+\.\d+\.\d+)\b", re.ASCII)


class CodexProbe(Protocol):
    """Injection seam so discovery tests never touch a real PATH or binary."""

    def path_entries(self) -> tuple[str, ...]: ...

    def run_version(self, executable: str) -> str | None: ...


def _default_version_runner(executable: str) -> str | None:
    try:
        completed = subprocess.run(
            (executable, "--version"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if completed.returncode != 0:
        return None
    try:
        text = completed.stdout[:_VERSION_OUTPUT_LIMIT].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return text


def _windows_codex_app_directories() -> tuple[str, ...]:
    """Resolve the Store-installed Codex App resource directory without mutating it."""

    command = (
        "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
        "Get-AppxPackage | Where-Object { $_.PackageFamilyName -eq "
        f"'{_WINDOWS_CODEX_PACKAGE_FAMILY}' }} | "
        "Select-Object -ExpandProperty InstallLocation"
    )
    try:
        completed = subprocess.run(
            ("powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_WINDOWS_APP_QUERY_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except OSError, subprocess.SubprocessError:
        return ()
    if completed.returncode != 0:
        return ()
    try:
        output = completed.stdout[:_VERSION_OUTPUT_LIMIT].decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return ()
    directories: list[str] = []
    for raw_line in output.splitlines():
        root = raw_line.strip()
        if not root or len(root) > 4_096 or any(ord(char) < 32 for char in root):
            continue
        directories.append(str(Path(root) / "resources"))
    return tuple(dict.fromkeys(directories))


def _standard_app_directories() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return _MACOS_CODEX_DIRECTORIES
    if sys.platform == "win32":
        return _windows_codex_app_directories()
    return ()


def _candidate_name_groups() -> tuple[tuple[str, ...], ...]:
    """Return one ordered executable-name group per logical Codex installation."""

    suffixes = (".exe", ".cmd", "") if sys.platform == "win32" else ("",)
    return tuple(
        tuple(f"{stem}{suffix}" for suffix in suffixes) for stem in _CODEX_EXECUTABLE_STEMS
    )


@dataclass(frozen=True, slots=True)
class _DefaultProbe:
    version_runner: Callable[[str], str | None]

    def path_entries(self) -> tuple[str, ...]:
        raw = os.environ.get("PATH", "")
        entries = [entry for entry in raw.split(os.pathsep) if entry]
        for directory in _standard_app_directories():
            if directory not in entries:
                entries.append(directory)
        return tuple(entries)

    def run_version(self, executable: str) -> str | None:
        return self.version_runner(executable)


def _parse_version(output: str | None) -> str | None:
    if output is None:
        return None
    first_line = output.splitlines()[0] if output.splitlines() else ""
    match = _VERSION_RE.search(first_line)
    return None if match is None else match.group(1)


def discover_codex_binaries(*, _probe: CodexProbe | None = None) -> tuple[HarnessBinary, ...]:
    """Enumerate reviewed Codex executable names on PATH, version-probed and bounded.

    Discovery is pure observation: it mutates nothing, claims no capability support
    (every candidate reports ``untested`` compatibility per E-002), and returns a
    deterministic result sorted by resolved path.
    """

    probe: CodexProbe = _DefaultProbe(_default_version_runner) if _probe is None else _probe
    seen: set[str] = set()
    binaries: list[HarnessBinary] = []
    for entry in probe.path_entries():
        for executable_names in _candidate_name_groups():
            for executable_name in executable_names:
                candidate = Path(os.path.abspath(Path(entry) / executable_name))
                try:
                    resolved = str(candidate.resolve(strict=True))
                except OSError:
                    continue
                if resolved in seen:
                    break
                if not candidate.is_file() or not os.access(candidate, os.X_OK):
                    continue
                seen.add(resolved)
                version = _parse_version(probe.run_version(str(candidate)))
                binaries.append(
                    HarnessBinary(
                        harness_id=HarnessId.CODEX,
                        # The visible name is what registration must invoke; the
                        # resolved target is only a dedupe key for aliased installs.
                        executable_path=str(candidate),
                        reported_version=version,
                        compatibility="untested",
                    )
                )
                if len(binaries) >= _MAX_CANDIDATES:
                    binaries.sort(key=lambda binary: binary.executable_path)
                    return tuple(binaries)
                break
    binaries.sort(key=lambda binary: binary.executable_path)
    return tuple(binaries)
