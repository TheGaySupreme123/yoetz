"""Enforcing process sandbox boundary for approved checks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

__all__ = [
    "CheckSandboxLaunch",
    "CheckSandboxPort",
    "CheckSandboxStatus",
]


class CheckSandboxStatus(str, Enum):  # noqa: UP042 - exact wire enum
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class CheckSandboxLaunch:
    """Prepared argv/env for one sandboxed check execution."""

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    status: CheckSandboxStatus
    network_isolated: bool

    def __post_init__(self) -> None:
        if type(self.argv) is not tuple or not self.argv:
            raise ValueError("check_sandbox_invalid")
        if type(self.status) is not CheckSandboxStatus:
            raise ValueError("check_sandbox_invalid")
        if type(self.network_isolated) is not bool:
            raise ValueError("check_sandbox_invalid")
        if self.status is CheckSandboxStatus.UNAVAILABLE and self.network_isolated:
            raise ValueError("check_sandbox_invalid")


class CheckSandboxPort(Protocol):
    """Wrap an approved argv under an enforcing no-network sandbox when available."""

    def prepare(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        deny_network: bool,
    ) -> CheckSandboxLaunch: ...
