"""Unit tests for commitment-bound approved check runner."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

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


def _approval(argv: tuple[str, ...]) -> ApprovedCheckApproval:
    commitment = approval_commitment("pytest-unit", argv, allow_network=False)
    return ApprovedCheckApproval(
        approval_id="pytest-unit",
        argv=argv,
        allow_network=False,
        timeout_seconds=10.0,
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
