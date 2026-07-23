"""Platform CheckSandboxPort adapters (macOS seatbelt, Linux bwrap when present)."""

from __future__ import annotations

import platform
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from yoetz.ports.check_sandbox import CheckSandboxLaunch, CheckSandboxStatus

__all__ = [
    "LinuxCheckSandbox",
    "MacOSCheckSandbox",
    "UnsupportedCheckSandbox",
    "default_check_sandbox",
]

_SEATBELT_NO_NETWORK = """\
(version 1)
(allow default)
(deny network*)
"""


class UnsupportedCheckSandbox:
    """Honest unavailable sandbox — never claims network denial from env alone."""

    def prepare(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        deny_network: bool,
    ) -> CheckSandboxLaunch:
        _ = deny_network
        return CheckSandboxLaunch(
            argv=tuple(argv),
            env=dict(env),
            cwd=cwd,
            status=CheckSandboxStatus.UNAVAILABLE,
            network_isolated=False,
        )


class MacOSCheckSandbox:
    """Enforcing no-network Seatbelt profile via sandbox-exec when available."""

    def __init__(self, *, sandbox_exec: str | None = None) -> None:
        self._sandbox_exec = sandbox_exec or shutil.which("sandbox-exec")

    def prepare(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        deny_network: bool,
    ) -> CheckSandboxLaunch:
        if not deny_network:
            return CheckSandboxLaunch(
                argv=tuple(argv),
                env=dict(env),
                cwd=cwd,
                status=CheckSandboxStatus.READY,
                network_isolated=False,
            )
        if self._sandbox_exec is None or not Path(self._sandbox_exec).is_file():
            return CheckSandboxLaunch(
                argv=tuple(argv),
                env=dict(env),
                cwd=cwd,
                status=CheckSandboxStatus.UNAVAILABLE,
                network_isolated=False,
            )
        home = env.get("HOME", "/tmp")
        tmpdir = env.get("TMPDIR", "/tmp")
        _ = home, tmpdir
        profile = _SEATBELT_NO_NETWORK
        profile_dir = Path(tempfile.mkdtemp(prefix="yoetz-sb-"))
        profile_path = profile_dir / "no-network.sb"
        profile_path.write_text(profile, encoding="ascii")
        wrapped = (self._sandbox_exec, "-f", str(profile_path), *argv)
        return CheckSandboxLaunch(
            argv=wrapped,
            env=dict(env),
            cwd=cwd,
            status=CheckSandboxStatus.READY,
            network_isolated=True,
        )


class LinuxCheckSandbox:
    """Enforcing no-network sandbox via bubblewrap when available."""

    def __init__(self, *, bwrap: str | None = None) -> None:
        self._bwrap = bwrap or shutil.which("bwrap")

    def prepare(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        deny_network: bool,
    ) -> CheckSandboxLaunch:
        if not deny_network:
            return CheckSandboxLaunch(
                argv=tuple(argv),
                env=dict(env),
                cwd=cwd,
                status=CheckSandboxStatus.READY,
                network_isolated=False,
            )
        if self._bwrap is None or not Path(self._bwrap).is_file():
            return CheckSandboxLaunch(
                argv=tuple(argv),
                env=dict(env),
                cwd=cwd,
                status=CheckSandboxStatus.UNAVAILABLE,
                network_isolated=False,
            )
        wrapped = (
            self._bwrap,
            "--die-with-parent",
            "--unshare-net",
            "--bind",
            str(cwd),
            str(cwd),
            "--chdir",
            str(cwd),
            *argv,
        )
        return CheckSandboxLaunch(
            argv=wrapped,
            env=dict(env),
            cwd=cwd,
            status=CheckSandboxStatus.READY,
            network_isolated=True,
        )


def default_check_sandbox() -> MacOSCheckSandbox | LinuxCheckSandbox | UnsupportedCheckSandbox:
    system = platform.system()
    if system == "Darwin":
        return MacOSCheckSandbox()
    if system == "Linux":
        return LinuxCheckSandbox()
    return UnsupportedCheckSandbox()
