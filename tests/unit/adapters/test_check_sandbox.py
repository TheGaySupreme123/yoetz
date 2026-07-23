"""Unit tests for CheckSandboxPort and approved-check sandbox integration."""

from __future__ import annotations

import platform
import shutil
from pathlib import Path

from yoetz.adapters.approved_checks import (
    ApprovedCheckApproval,
    ApprovedCheckCommand,
    ApprovedCheckOutcome,
    ApprovedCheckRunner,
    ApprovedCheckStatus,
    approval_commitment,
)
from yoetz.adapters.check_sandbox import (
    MacOSCheckSandbox,
    UnsupportedCheckSandbox,
    default_check_sandbox,
)
from yoetz.adapters.workspace_inspect import open_inspect_workspace
from yoetz.ports.check_sandbox import CheckSandboxStatus

_TRUE = shutil.which("true") or "/usr/bin/true"


def _approval(argv: tuple[str, ...]) -> ApprovedCheckApproval:
    commitment = approval_commitment("pytest-sandbox", argv, allow_network=False)
    return ApprovedCheckApproval(
        approval_id="pytest-sandbox",
        argv=argv,
        allow_network=False,
        timeout_seconds=10.0,
        approval_commitment=commitment,
    )


def test_default_sandbox_is_platform_specific() -> None:
    sandbox = default_check_sandbox()
    if platform.system() == "Darwin":
        assert isinstance(sandbox, MacOSCheckSandbox)
    launch = sandbox.prepare(
        argv=(_TRUE,),
        cwd=Path("/"),
        env={"HOME": "/tmp", "TMPDIR": "/tmp"},
        deny_network=True,
    )
    if platform.system() == "Darwin":
        assert launch.status is CheckSandboxStatus.READY
        assert launch.network_isolated is True
        assert launch.argv[0].endswith("sandbox-exec")


def test_unsupported_sandbox_never_claims_network_isolation() -> None:
    launch = UnsupportedCheckSandbox().prepare(
        argv=(_TRUE,),
        cwd=Path("/"),
        env={"YOETZ_APPROVED_CHECK_NETWORK": "denied"},
        deny_network=True,
    )
    assert launch.status is CheckSandboxStatus.UNAVAILABLE
    assert launch.network_isolated is False


def test_approved_true_succeeds_inside_enforcing_sandbox(tmp_path: Path) -> None:
    if platform.system() != "Darwin":
        return
    handle = open_inspect_workspace(tmp_path)
    approval = _approval((_TRUE,))
    runner = ApprovedCheckRunner({approval.approval_commitment: approval})
    result = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest="sha256:" + "a" * 64,
            expected_subject_state_digest="sha256:" + "a" * 64,
        )
    )
    assert result.status is ApprovedCheckStatus.PASSED
    assert result.outcome is ApprovedCheckOutcome.SUCCESS


def test_sandbox_unavailable_rejects_honestly(tmp_path: Path) -> None:
    handle = open_inspect_workspace(tmp_path)
    approval = _approval((_TRUE,))
    runner = ApprovedCheckRunner(
        {approval.approval_commitment: approval},
        sandbox=UnsupportedCheckSandbox(),
    )
    result = runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest="sha256:" + "b" * 64,
        )
    )
    assert result.status is ApprovedCheckStatus.REJECTED
    assert result.outcome is ApprovedCheckOutcome.SANDBOX_UNAVAILABLE
