"""Live Cursor MCP process identity, classified without retaining argv bytes.

Installed plugin files name a desired route. Cursor can keep a shared ``mcp-process`` helper
alive across Reload Window, so file status alone cannot prove that model calls use that route.
This module classifies exact known ``yoetz mcp serve`` argv suffixes and Cursor-helper parents
into counts and an activation token. Unmatched tokens are dropped and never logged.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

__all__ = [
    "CursorMcpProcessPort",
    "CursorMcpProcessSnapshot",
    "CursorMcpRuntimeObservation",
    "FixedCursorMcpProcesses",
    "OsCursorMcpProcesses",
    "classify_cursor_semantic_ceiling",
    "observe_cursor_mcp_runtime",
]

_MAX_PROCESSES: Final = 64
_MAX_TOKENS: Final = 12
_MAX_COMM: Final = 32
_PS_EXECUTABLE: Final = "/bin/ps"
_POLICY_SUFFIXES: Final = frozenset(
    {
        ("mcp", "serve"),
        ("mcp", "serve", "--host", "cursor"),
    }
)
_STRICT_SUFFIXES: Final = frozenset(
    {
        ("mcp", "serve", "--semantic", "off"),
        ("mcp", "serve", "--host", "cursor", "--semantic", "off"),
    }
)
_CURSOR_COMM_PREFIXES: Final = ("Cursor", "Cursor Helper")
_CURSOR_COMM_EXACT: Final = frozenset({"Cursor", "mcp-process"})


type CursorRuntimeActivation = Literal["unobserved", "matched", "full_restart_required"]
type CursorCeilingClass = Literal[
    "not_applicable",
    "genuine_route_ceiling",
    "activation_mismatch",
    "activation_unproven",
]


@dataclass(frozen=True, slots=True)
class CursorMcpProcessSnapshot:
    """One classified live process. ``argv_suffix`` is an exact known serve suffix or empty."""

    parent_kind: Literal["cursor_helper", "other"]
    route_profile: Literal["strict", "policy"] | None

    def __post_init__(self) -> None:
        if self.parent_kind not in {"cursor_helper", "other"}:
            raise ValueError("cursor_mcp_process_invalid")
        if self.route_profile not in {None, "strict", "policy"}:
            raise ValueError("cursor_mcp_process_invalid")


@dataclass(frozen=True, slots=True)
class CursorMcpRuntimeObservation:
    observed: bool
    activation: CursorRuntimeActivation
    live_route_profile: Literal["strict", "policy"] | None
    policy_process_count: int
    strict_process_count: int
    foreign_process_count: int

    def __post_init__(self) -> None:
        if type(self.observed) is not bool:
            raise ValueError("cursor_mcp_runtime_invalid")
        if self.activation not in {"unobserved", "matched", "full_restart_required"}:
            raise ValueError("cursor_mcp_runtime_invalid")
        if self.live_route_profile not in {None, "strict", "policy"}:
            raise ValueError("cursor_mcp_runtime_invalid")
        for count in (
            self.policy_process_count,
            self.strict_process_count,
            self.foreign_process_count,
        ):
            if type(count) is not int or count < 0 or count > _MAX_PROCESSES:
                raise ValueError("cursor_mcp_runtime_invalid")
        if self.activation == "unobserved" and self.live_route_profile is not None:
            raise ValueError("cursor_mcp_runtime_invalid")
        if self.activation == "matched" and self.live_route_profile not in {"strict", "policy"}:
            raise ValueError("cursor_mcp_runtime_invalid")


class CursorMcpProcessPort(Protocol):
    def snapshot(self) -> tuple[CursorMcpProcessSnapshot, ...] | None:
        """Return classified processes, or ``None`` when the scan itself is unavailable."""


@dataclass(frozen=True, slots=True)
class FixedCursorMcpProcesses:
    processes: tuple[CursorMcpProcessSnapshot, ...] | None

    def snapshot(self) -> tuple[CursorMcpProcessSnapshot, ...] | None:
        return self.processes


def _cursor_helper_comm(value: str) -> bool:
    if type(value) is not str or not value:
        return False
    value = value.rsplit("/", 1)[-1]
    if not value or len(value) > _MAX_COMM:
        return False
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return False
    if value in _CURSOR_COMM_EXACT or value == "cursor":
        return True
    return (value.startswith(_CURSOR_COMM_PREFIXES) or value.startswith("cursor-helper")) and all(
        char.isalnum() or char in {" ", "(", ")", "-"} for char in value
    )


def _yoetz_launcher_token(value: str) -> bool:
    if type(value) is not str or not value or len(value) > 4_096:
        return False
    return value.replace("\\", "/").rsplit("/", 1)[-1] in {"yoetz", "yoetz.exe"}


def classify_serve_suffix(
    tokens: Sequence[str],
) -> Literal["strict", "policy", "foreign"] | None:
    """Classify a serve suffix. Returns ``None`` when ``mcp serve`` is absent."""

    suffix: tuple[str, ...] | None = None
    for index, token in enumerate(tokens):
        if type(token) is not str:
            return None
        if (
            token == "mcp"
            and index > 0
            and _yoetz_launcher_token(tokens[index - 1])
            and index + 1 < len(tokens)
            and tokens[index + 1] == "serve"
        ):
            rest = tuple(tokens[index:])
            if len(rest) > _MAX_TOKENS:
                return "foreign"
            suffix = rest
    if suffix is None:
        return None
    if suffix in _POLICY_SUFFIXES:
        return "policy"
    if suffix in _STRICT_SUFFIXES:
        return "strict"
    return "foreign"


def observe_cursor_mcp_runtime(
    *,
    installed_route: Literal["strict", "policy"] | None,
    processes: CursorMcpProcessPort,
) -> CursorMcpRuntimeObservation:
    """Compare classified Cursor-helper children with the installed winning route."""

    snapshot = processes.snapshot()
    if snapshot is None:
        return CursorMcpRuntimeObservation(False, "unobserved", None, 0, 0, 0)
    policy = 0
    strict = 0
    foreign = 0
    helper_routes: set[Literal["strict", "policy"]] = set()
    helper_foreign = False
    scan_truncated = len(snapshot) > _MAX_PROCESSES
    for item in snapshot[:_MAX_PROCESSES]:
        if type(item) is not CursorMcpProcessSnapshot:
            continue
        if item.route_profile == "policy":
            policy += 1
            if item.parent_kind == "cursor_helper":
                helper_routes.add("policy")
        elif item.route_profile == "strict":
            strict += 1
            if item.parent_kind == "cursor_helper":
                helper_routes.add("strict")
        else:
            foreign += 1
            if item.parent_kind == "cursor_helper":
                helper_foreign = True
    if scan_truncated or (not helper_routes and not helper_foreign):
        return CursorMcpRuntimeObservation(True, "unobserved", None, policy, strict, foreign)
    live: Literal["strict", "policy"] | None
    if len(helper_routes) == 1 and not helper_foreign:
        live = next(iter(helper_routes))
    else:
        live = None
    if live is not None and live == installed_route:
        activation: CursorRuntimeActivation = "matched"
    else:
        activation = "full_restart_required"
    return CursorMcpRuntimeObservation(True, activation, live, policy, strict, foreign)


def classify_cursor_semantic_ceiling(
    *,
    semantic_status: str,
    semantic_reason: str,
    installed_route: Literal["strict", "policy"] | None,
    runtime: CursorMcpRuntimeObservation,
) -> CursorCeilingClass:
    """Distinguish a live strict ceiling from installed-policy vs stale-runtime mismatch."""

    if semantic_status != "blocked_by_policy" or semantic_reason != "route_semantic_ceiling":
        return "not_applicable"
    if installed_route == "strict" and runtime.activation != "full_restart_required":
        return "genuine_route_ceiling"
    if installed_route == "policy":
        if runtime.activation == "full_restart_required":
            return "activation_mismatch"
        if runtime.activation == "matched" and runtime.live_route_profile == "policy":
            return "activation_mismatch"
        return "activation_unproven"
    if runtime.activation == "full_restart_required":
        return "activation_mismatch"
    return "genuine_route_ceiling"


def _linux_snapshots() -> tuple[CursorMcpProcessSnapshot, ...] | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    try:
        pid_dirs = tuple(path for path in proc.iterdir() if path.name.isdigit())
    except OSError:
        return None
    comm_by_pid: dict[int, str] = {}
    ppid_by_pid: dict[int, int] = {}
    classified: list[CursorMcpProcessSnapshot] = []
    for path in pid_dirs:
        try:
            pid = int(path.name)
        except ValueError:
            continue
        try:
            comm = path.joinpath("comm").read_text(encoding="ascii", errors="ignore").strip()
        except OSError:
            continue
        comm_by_pid[pid] = comm
        try:
            status_text = path.joinpath("status").read_text(encoding="ascii", errors="ignore")
            for line in status_text.splitlines():
                if line.startswith("PPid:"):
                    ppid_by_pid[pid] = int(line.split(":", 1)[1].strip())
                    break
        except OSError, ValueError:
            continue
    for path in pid_dirs:
        if len(classified) > _MAX_PROCESSES:
            break
        try:
            raw = path.joinpath("cmdline").read_bytes()
        except OSError:
            continue
        if not raw or len(raw) > 4096:
            continue
        tokens = tuple(part.decode("utf-8", errors="ignore") for part in raw.split(b"\0") if part)
        kind = classify_serve_suffix(tokens)
        if kind is None:
            continue
        try:
            pid = int(path.name)
        except ValueError:
            continue
        parent = ppid_by_pid.get(pid)
        parent_comm = comm_by_pid.get(parent, "") if parent is not None else ""
        grand = ppid_by_pid.get(parent, 0) if parent is not None else 0
        grand_comm = comm_by_pid.get(grand, "") if grand else ""
        helper = _cursor_helper_comm(parent_comm) or _cursor_helper_comm(grand_comm)
        classified.append(
            CursorMcpProcessSnapshot(
                "cursor_helper" if helper else "other",
                None if kind == "foreign" else kind,
            )
        )
    return tuple(classified)


def _parse_ps_table(payload: bytes) -> tuple[tuple[int, int, str], ...]:
    rows: list[tuple[int, int, str]] = []
    for raw_line in payload.splitlines()[: 8 * _MAX_PROCESSES]:
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rest = parts[2] if len(parts) == 3 else ""
        rows.append((pid, ppid, rest[:512]))
    return tuple(rows)


def _darwin_snapshots() -> tuple[CursorMcpProcessSnapshot, ...] | None:
    try:
        comm_table = subprocess.run(
            [_PS_EXECUTABLE, "-ax", "-o", "pid=", "-o", "ppid=", "-o", "comm="],
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            env={"LANG": "C", "LC_ALL": "C"},
            shell=False,
            timeout=2,
        )
        arg_table = subprocess.run(
            [_PS_EXECUTABLE, "-axww", "-o", "pid=", "-o", "ppid=", "-o", "args="],
            stdin=subprocess.DEVNULL,
            check=False,
            capture_output=True,
            env={"LANG": "C", "LC_ALL": "C"},
            shell=False,
            timeout=2,
        )
    except OSError, ValueError, subprocess.SubprocessError:
        return None
    if comm_table.returncode != 0 or arg_table.returncode != 0:
        return None
    comm_by_pid = {pid: comm for pid, _ppid, comm in _parse_ps_table(comm_table.stdout)}
    ppid_by_pid = {pid: ppid for pid, ppid, _comm in _parse_ps_table(comm_table.stdout)}
    classified: list[CursorMcpProcessSnapshot] = []
    for pid, _ppid, args in _parse_ps_table(arg_table.stdout):
        if len(classified) > _MAX_PROCESSES:
            break
        tokens = tuple(args.split())
        kind = classify_serve_suffix(tokens)
        if kind is None:
            continue
        parent = ppid_by_pid.get(pid)
        parent_comm = comm_by_pid.get(parent, "") if parent is not None else ""
        grand = ppid_by_pid.get(parent, 0) if parent is not None else 0
        grand_comm = comm_by_pid.get(grand, "") if grand else ""
        helper = _cursor_helper_comm(parent_comm) or _cursor_helper_comm(grand_comm)
        classified.append(
            CursorMcpProcessSnapshot(
                "cursor_helper" if helper else "other",
                None if kind == "foreign" else kind,
            )
        )
    return tuple(classified)


class OsCursorMcpProcesses:
    def snapshot(self) -> tuple[CursorMcpProcessSnapshot, ...] | None:
        try:
            linux = _linux_snapshots()
            if linux is not None:
                return linux
            return _darwin_snapshots()
        except OSError, ValueError, TimeoutError:
            return None
