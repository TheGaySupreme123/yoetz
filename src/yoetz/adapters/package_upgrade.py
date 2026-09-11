"""Bounded uv package-manager boundary for explicit upgrades."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Final

PACKAGE_UPGRADE_ARGV: Final = ("uv", "tool", "upgrade", "yoetz")


def execute_package_upgrade() -> str:
    """Upgrade only the invoking ambient uv tool; refuse source/pinned/isolated runtimes."""
    from yoetz.config.paths import isolated_root

    if isolated_root() is not None:
        return "refused_isolated_runtime"
    try:
        probe = subprocess.run(  # noqa: S603 - fixed local uv metadata command
            ("uv", "tool", "dir"),
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if probe.returncode != 0 or len(probe.stdout) > 4096:
            return "package_manager_unavailable"
        tool_root = Path(os.fsdecode(probe.stdout).strip())
        if (
            not tool_root.is_absolute()
            or Path(sys.prefix).resolve() != (tool_root / "yoetz").resolve()
        ):
            return "refused_non_uv_tool_runtime"
        # Do not capture arbitrary package-manager output or forward it into structural errors.
        result = subprocess.run(  # noqa: S603 - fixed user-authorized package operation
            PACKAGE_UPGRADE_ARGV,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return "outcome_unknown"
    except OSError, ValueError:
        return "package_manager_unavailable"
    return "package_command_succeeded" if result.returncode == 0 else "package_command_failed"
