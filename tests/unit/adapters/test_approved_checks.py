"""Unit tests for commitment-bound approved check runner."""

from __future__ import annotations

import os
import shutil
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from yoetz.adapters.approved_checks import (
    ApprovedCheckApproval,
    ApprovedCheckCommand,
    ApprovedCheckOutcome,
    ApprovedCheckRunner,
    ApprovedCheckStatus,
    approval_commitment,
)
from yoetz.adapters.workspace_inspect import open_inspect_workspace
from yoetz.ports.check_sandbox import CheckSandboxLaunch, CheckSandboxStatus

_TRUE = shutil.which("true") or "/usr/bin/true"


class _ReadyCheckSandbox:
    """Deterministic unit seam; platform-enforcement behavior has dedicated adapter tests."""

    def prepare(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        deny_network: bool,
    ) -> CheckSandboxLaunch:
        return CheckSandboxLaunch(
            argv=tuple(argv),
            env=dict(env),
            cwd=cwd,
            status=CheckSandboxStatus.READY,
            network_isolated=deny_network,
        )


def _approval(argv: tuple[str, ...], *, timeout_seconds: float = 10.0) -> ApprovedCheckApproval:
    commitment = approval_commitment("pytest-unit", argv, allow_network=False)
    return ApprovedCheckApproval(
        approval_id="pytest-unit",
        argv=argv,
        allow_network=False,
        timeout_seconds=timeout_seconds,
        approval_commitment=commitment,
    )


def test_approved_check_binds_to_subject_state_digest(tmp_path: Path) -> None:
    handle = open_inspect_workspace(tmp_path)
    argv = (_TRUE,)
    approval = _approval(argv)
    runner = ApprovedCheckRunner(
        {approval.approval_commitment: approval},
        sandbox=_ReadyCheckSandbox(),
    )
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64
    ok = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest=digest_a,
            expected_subject_state_digest=digest_a,
        )
    )
    assert ok.status is ApprovedCheckStatus.PASSED
    assert ok.subject_state_digest == digest_a
    stale = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest=digest_b,
            expected_subject_state_digest=digest_a,
        )
    )
    assert stale.status is ApprovedCheckStatus.STALE
    assert stale.outcome is ApprovedCheckOutcome.SUBJECT_STATE_MISMATCH


def test_unapproved_argv_rejected(tmp_path: Path) -> None:
    handle = open_inspect_workspace(tmp_path)
    approval = _approval((_TRUE,))
    runner = ApprovedCheckRunner()  # nothing registered
    result = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest="sha256:" + "c" * 64,
        )
    )
    assert result.status is ApprovedCheckStatus.REJECTED
    assert result.outcome is ApprovedCheckOutcome.NOT_APPROVED


def test_output_never_retained_as_secret_text(tmp_path: Path) -> None:
    handle = open_inspect_workspace(tmp_path)
    argv = ("/bin/echo", "SECRET=should-not-leak")
    # echo may not allow '=' in our argv validator — use a safer token.
    argv = ("/bin/echo", "secret-token-value")
    approval = _approval(argv)
    runner = ApprovedCheckRunner(
        {approval.approval_commitment: approval},
        sandbox=_ReadyCheckSandbox(),
    )
    result = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest="sha256:" + "d" * 64,
        )
    )
    assert result.output_digest is not None
    assert "secret-token-value" not in repr(result)
    assert "SECRET" not in repr(result)


def test_nonforking_timeout_is_deterministic(tmp_path: Path) -> None:
    handle = open_inspect_workspace(tmp_path)
    sleep = shutil.which("sleep") or "/bin/sleep"
    approval = _approval((sleep, "8"), timeout_seconds=0.3)
    runner = ApprovedCheckRunner(
        {approval.approval_commitment: approval},
        sandbox=_ReadyCheckSandbox(),
    )
    started = time.monotonic()
    result = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest="sha256:" + "e" * 64,
        )
    )
    elapsed = time.monotonic() - started
    assert result.status is ApprovedCheckStatus.TIMEOUT
    assert result.outcome is ApprovedCheckOutcome.TIMEOUT
    assert result.exit_status is None
    assert result.output_digest is None
    assert result.output_bytes == 0
    assert elapsed < 4.0


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group reaping")
def test_timeout_reaps_forked_descendant_before_result(tmp_path: Path) -> None:
    handle = open_inspect_workspace(tmp_path)
    pid_path = tmp_path / "desc.pid"
    stop_path = tmp_path / "stop.flag"
    leak_path = tmp_path / "leaked.txt"
    script_path = tmp_path / "fork-check.sh"
    script_path.write_text(
        "#!/bin/sh\n"
        "pid_file=$1\n"
        "stop_file=$2\n"
        "leak_file=$3\n"
        "(\n"
        "  trap '' TERM\n"
        '  while [ ! -f "$stop_file" ]; do\n'
        "    sleep 0.05\n"
        "  done\n"
        '  echo leaked > "$leak_file"\n'
        ") &\n"
        'echo $! > "$pid_file"\n'
        # Exit the direct child while its descendant keeps the inherited output pipes open. This
        # proves the runner retains the launch-time group identity instead of relying on a later
        # lookup through a leader that may already be gone.
        "exit 0\n",
        encoding="ascii",
    )
    os.chmod(script_path, 0o700)
    approval = _approval(
        ("/bin/sh", str(script_path), str(pid_path), str(stop_path), str(leak_path)),
        timeout_seconds=0.5,
    )
    runner = ApprovedCheckRunner(
        {approval.approval_commitment: approval},
        sandbox=_ReadyCheckSandbox(),
    )
    result = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest="sha256:" + "f" * 64,
        )
    )
    assert result.status is ApprovedCheckStatus.TIMEOUT
    assert result.outcome is ApprovedCheckOutcome.TIMEOUT
    assert pid_path.is_file()
    descendant_pid = int(pid_path.read_text(encoding="ascii").strip())
    with pytest.raises(ProcessLookupError):
        os.kill(descendant_pid, 0)
    stop_path.write_text("stop", encoding="ascii")
    time.sleep(0.3)
    assert not leak_path.exists()
