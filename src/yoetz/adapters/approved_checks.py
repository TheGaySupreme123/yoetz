"""Approved-check runner: fixed argv, commitment-bound, no freeform shell."""

from __future__ import annotations

import hashlib
import os
import selectors
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from yoetz.ports.subject_state import LocalWorkspaceHandle
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "APPROVED_CHECK_FORMAT",
    "ApprovedCheckApproval",
    "ApprovedCheckCommand",
    "ApprovedCheckOutcome",
    "ApprovedCheckResult",
    "ApprovedCheckRunner",
    "ApprovedCheckStatus",
    "approval_commitment",
]

APPROVED_CHECK_FORMAT: Final = "yoetz.approved-check/1"
_DEFAULT_TIMEOUT_SECONDS: Final = 30.0
_MAX_OUTPUT_BYTES: Final = 65_536
_MAX_ARGV: Final = 32
_MAX_ARG_BYTES: Final = 512
_TOKEN_CHARS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/+-="
)


class ApprovedCheckStatus(str, Enum):  # noqa: UP042 - exact wire enum
    PASSED = "passed"
    FAILED = "failed"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    STALE = "stale"


class ApprovedCheckOutcome(str, Enum):  # noqa: UP042 - exact wire enum
    SUCCESS = "success"
    NONZERO_EXIT = "nonzero_exit"
    NOT_APPROVED = "not_approved"
    UNSAFE_ARGV = "unsafe_argv"
    NETWORK_DENIED = "network_denied"
    OUTPUT_TRUNCATED = "output_truncated"
    TIMEOUT = "timeout"
    EXEC_FAILED = "exec_failed"
    SUBJECT_STATE_MISMATCH = "subject_state_mismatch"


@dataclass(frozen=True, slots=True)
class ApprovedCheckApproval:
    """Commitment-backed approval for one exact argv under a workspace policy."""

    approval_id: str
    argv: tuple[str, ...]
    allow_network: bool
    timeout_seconds: float
    approval_commitment: str

    def __post_init__(self) -> None:
        if (
            type(self.approval_id) is not str
            or not self.approval_id
            or len(self.approval_id) > 128
            or type(self.argv) is not tuple
            or not 1 <= len(self.argv) <= _MAX_ARGV
            or type(self.allow_network) is not bool
            or type(self.timeout_seconds) is not float
            or not 0.1 <= self.timeout_seconds <= 300.0
            or type(self.approval_commitment) is not str
            or not self.approval_commitment.startswith("sha256:")
        ):
            raise ProtocolValueError("invalid_approved_check")
        for arg in self.argv:
            if (
                type(arg) is not str
                or not arg
                or len(arg.encode("utf-8")) > _MAX_ARG_BYTES
                or any(ch not in _TOKEN_CHARS and ch not in {":", "@"} for ch in arg)
            ):
                raise ProtocolValueError("invalid_approved_check")
        expected = approval_commitment(
            self.approval_id, self.argv, allow_network=self.allow_network
        )
        if self.approval_commitment != expected:
            raise ProtocolValueError("invalid_approved_check")


@dataclass(frozen=True, slots=True)
class ApprovedCheckCommand:
    workspace: LocalWorkspaceHandle
    approval: ApprovedCheckApproval
    subject_state_digest: str
    expected_subject_state_digest: str | None = None

    def __post_init__(self) -> None:
        if type(self.workspace) is not LocalWorkspaceHandle or not self.workspace.is_validated():
            raise ProtocolValueError("invalid_approved_check")
        if type(self.approval) is not ApprovedCheckApproval:
            raise ProtocolValueError("invalid_approved_check")
        for digest in (self.subject_state_digest, self.expected_subject_state_digest):
            if digest is None:
                continue
            if type(digest) is not str or not digest.startswith("sha256:") or len(digest) != 71:
                raise ProtocolValueError("invalid_approved_check")


@dataclass(frozen=True, slots=True)
class ApprovedCheckResult:
    status: ApprovedCheckStatus
    outcome: ApprovedCheckOutcome
    exit_status: int | None
    output_digest: str | None
    output_bytes: int
    subject_state_digest: str
    approval_commitment: str
    result_digest: str
    duration_ms: int

    def __post_init__(self) -> None:
        if type(self.status) is not ApprovedCheckStatus:
            raise ProtocolValueError("invalid_approved_check")
        if type(self.outcome) is not ApprovedCheckOutcome:
            raise ProtocolValueError("invalid_approved_check")
        if self.exit_status is not None and (
            type(self.exit_status) is not int or not 0 <= self.exit_status <= 255
        ):
            raise ProtocolValueError("invalid_approved_check")
        if self.output_digest is not None and (
            type(self.output_digest) is not str
            or not self.output_digest.startswith("sha256:")
            or len(self.output_digest) != 71
        ):
            raise ProtocolValueError("invalid_approved_check")
        if type(self.output_bytes) is not int or not 0 <= self.output_bytes <= _MAX_OUTPUT_BYTES:
            raise ProtocolValueError("invalid_approved_check")
        if (
            type(self.subject_state_digest) is not str
            or not self.subject_state_digest.startswith("sha256:")
            or type(self.approval_commitment) is not str
            or not self.approval_commitment.startswith("sha256:")
            or type(self.result_digest) is not str
            or not self.result_digest.startswith("sha256:")
            or type(self.duration_ms) is not int
            or self.duration_ms < 0
        ):
            raise ProtocolValueError("invalid_approved_check")


def approval_commitment(approval_id: str, argv: Sequence[str], *, allow_network: bool) -> str:
    """Return the commitment for one exact approved argv (not freeform shell)."""

    return canonical_digest(
        {
            "format": APPROVED_CHECK_FORMAT,
            "approval_id": approval_id,
            "argv": tuple(argv),
            "allow_network": allow_network,
        }
    )


def _sanitized_env(*, allow_network: bool) -> dict[str, str]:
    env = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "HOME": os.devnull,
        "TMPDIR": os.devnull,
        "PAGER": "cat",
        "TERM": "dumb",
    }
    if not allow_network:
        # Best-effort isolation markers; runner still rejects network-allowing approvals
        # unless the approval explicitly grants network.
        env["YOETZ_APPROVED_CHECK_NETWORK"] = "denied"
    return env


def _root_from_handle(workspace: LocalWorkspaceHandle) -> Path | None:
    try:
        descriptor = workspace._validated_descriptor()  # pyright: ignore[reportPrivateUsage]
    except ValueError:
        return None
    root = getattr(descriptor, "root", None)
    if isinstance(root, Path):
        return root
    return None


def _bounded_communicate(
    process: subprocess.Popen[bytes],
    *,
    stdout_limit: int,
    timeout_seconds: float,
) -> tuple[bytearray, bool]:
    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    stdout = bytearray()
    truncated = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            events = selector.select(timeout=remaining)
            if not events:
                continue
            for key, _ in events:
                chunk = os.read(key.fd, 8_192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.fileobj is process.stdout:
                    room = stdout_limit - len(stdout)
                    if room <= 0:
                        truncated = True
                        continue
                    if len(chunk) > room:
                        stdout.extend(chunk[:room])
                        truncated = True
                    else:
                        stdout.extend(chunk)
                # stderr is discarded after consumption; never retained for advice/status.
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
    finally:
        selector.close()
    return stdout, truncated


class ApprovedCheckRunner:
    """Execute only commitment-approved argv under a consented workspace root."""

    def __init__(self, approvals: Mapping[str, ApprovedCheckApproval] | None = None) -> None:
        self._approvals = dict(approvals or {})

    def register(self, approval: ApprovedCheckApproval) -> None:
        self._approvals[approval.approval_commitment] = approval

    def run(self, command: ApprovedCheckCommand) -> ApprovedCheckResult:
        if type(command) is not ApprovedCheckCommand:
            raise ProtocolValueError("invalid_approved_check")
        approval = command.approval
        registered = self._approvals.get(approval.approval_commitment)
        if registered is None or registered != approval:
            return self._result(
                ApprovedCheckStatus.REJECTED,
                ApprovedCheckOutcome.NOT_APPROVED,
                None,
                None,
                0,
                command.subject_state_digest,
                approval.approval_commitment,
                0,
            )
        if (
            command.expected_subject_state_digest is not None
            and command.expected_subject_state_digest != command.subject_state_digest
        ):
            return self._result(
                ApprovedCheckStatus.STALE,
                ApprovedCheckOutcome.SUBJECT_STATE_MISMATCH,
                None,
                None,
                0,
                command.subject_state_digest,
                approval.approval_commitment,
                0,
            )
        if approval.allow_network:
            # Network requires an explicit separate authorization; default runner denies.
            return self._result(
                ApprovedCheckStatus.REJECTED,
                ApprovedCheckOutcome.NETWORK_DENIED,
                None,
                None,
                0,
                command.subject_state_digest,
                approval.approval_commitment,
                0,
            )
        root = _root_from_handle(command.workspace)
        if root is None or not root.is_dir():
            return self._result(
                ApprovedCheckStatus.REJECTED,
                ApprovedCheckOutcome.EXEC_FAILED,
                None,
                None,
                0,
                command.subject_state_digest,
                approval.approval_commitment,
                0,
            )
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                list(approval.argv),
                cwd=root,
                env=_sanitized_env(allow_network=False),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
            )
            stdout, truncated = _bounded_communicate(
                process,
                stdout_limit=_MAX_OUTPUT_BYTES,
                timeout_seconds=approval.timeout_seconds,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            exit_status = int(process.returncode or 0)
            captured_len = len(stdout)
            # Digest only — never retain or return command output bytes.
            output_digest = "sha256:" + hashlib.sha256(bytes(stdout)).hexdigest()
            stdout[:] = b"\x00" * len(stdout)
            del stdout
            if truncated:
                outcome = ApprovedCheckOutcome.OUTPUT_TRUNCATED
                status = ApprovedCheckStatus.FAILED
            elif exit_status == 0:
                outcome = ApprovedCheckOutcome.SUCCESS
                status = ApprovedCheckStatus.PASSED
            else:
                outcome = ApprovedCheckOutcome.NONZERO_EXIT
                status = ApprovedCheckStatus.FAILED
            return self._result(
                status,
                outcome,
                exit_status,
                output_digest,
                captured_len,
                command.subject_state_digest,
                approval.approval_commitment,
                duration_ms,
            )
        except TimeoutError:
            if process is not None:
                process.kill()
                process.wait(timeout=5)
            return self._result(
                ApprovedCheckStatus.TIMEOUT,
                ApprovedCheckOutcome.TIMEOUT,
                None,
                None,
                0,
                command.subject_state_digest,
                approval.approval_commitment,
                int((time.monotonic() - started) * 1000),
            )
        except OSError:
            return self._result(
                ApprovedCheckStatus.REJECTED,
                ApprovedCheckOutcome.EXEC_FAILED,
                None,
                None,
                0,
                command.subject_state_digest,
                approval.approval_commitment,
                int((time.monotonic() - started) * 1000),
            )
        finally:
            if process is not None:
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()

    def _result(
        self,
        status: ApprovedCheckStatus,
        outcome: ApprovedCheckOutcome,
        exit_status: int | None,
        output_digest: str | None,
        output_bytes: int,
        subject_state_digest: str,
        approval_commitment_value: str,
        duration_ms: int,
    ) -> ApprovedCheckResult:
        payload: dict[str, JsonValue] = {
            "format": APPROVED_CHECK_FORMAT,
            "status": status.value,
            "outcome": outcome.value,
            "exit_status": exit_status,
            "output_digest": output_digest,
            "output_bytes": output_bytes,
            "subject_state_digest": subject_state_digest,
            "approval_commitment": approval_commitment_value,
            "duration_ms": duration_ms,
        }
        return ApprovedCheckResult(
            status=status,
            outcome=outcome,
            exit_status=exit_status,
            output_digest=output_digest,
            output_bytes=output_bytes,
            subject_state_digest=subject_state_digest,
            approval_commitment=approval_commitment_value,
            result_digest=canonical_digest(payload),
            duration_ms=duration_ms,
        )
