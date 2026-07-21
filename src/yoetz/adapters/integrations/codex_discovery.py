"""Read-only discovery of installed Codex CLI executables on the user PATH."""

from __future__ import annotations

import os
import re
import subprocess
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

_CODEX_EXECUTABLE_NAME: Final = "codex"
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


@dataclass(frozen=True, slots=True)
class _DefaultProbe:
    version_runner: Callable[[str], str | None]

    def path_entries(self) -> tuple[str, ...]:
        raw = os.environ.get("PATH", "")
        return tuple(entry for entry in raw.split(os.pathsep) if entry)

    def run_version(self, executable: str) -> str | None:
        return self.version_runner(executable)


def _parse_version(output: str | None) -> str | None:
    if output is None:
        return None
    first_line = output.splitlines()[0] if output.splitlines() else ""
    match = _VERSION_RE.search(first_line)
    return None if match is None else match.group(1)


def discover_codex_binaries(*, _probe: CodexProbe | None = None) -> tuple[HarnessBinary, ...]:
    """Enumerate distinct executable ``codex`` binaries on PATH, version-probed and bounded.

    Discovery is pure observation: it mutates nothing, claims no capability support
    (every candidate reports ``untested`` compatibility per E-002), and returns a
    deterministic result sorted by resolved path.
    """

    probe: CodexProbe = _DefaultProbe(_default_version_runner) if _probe is None else _probe
    seen: set[str] = set()
    binaries: list[HarnessBinary] = []
    for entry in probe.path_entries():
        candidate = Path(os.path.abspath(Path(entry) / _CODEX_EXECUTABLE_NAME))
        try:
            resolved = str(candidate.resolve(strict=True))
        except OSError:
            continue
        if resolved in seen:
            continue
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        seen.add(resolved)
        version = _parse_version(probe.run_version(str(candidate)))
        binaries.append(
            HarnessBinary(
                harness_id=HarnessId.CODEX,
                # The PATH-visible name is what registration must invoke; the
                # resolved target is only a dedupe key for aliased installs.
                executable_path=str(candidate),
                reported_version=version,
                compatibility="untested",
            )
        )
        if len(binaries) >= _MAX_CANDIDATES:
            break
    binaries.sort(key=lambda binary: binary.executable_path)
    return tuple(binaries)
