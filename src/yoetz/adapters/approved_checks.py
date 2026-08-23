"""Approved-check runner: fixed argv, commitment-bound, enforcing sandbox when available."""

from __future__ import annotations

import hashlib
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from yoetz.adapters.check_sandbox import default_check_sandbox
from yoetz.observability.privacy import redact_sensitive_content
from yoetz.ports.check_sandbox import CheckSandboxPort, CheckSandboxStatus
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
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
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


def _owner_private_temp_dirs() -> tuple[Path, Path, tempfile.TemporaryDirectory[str]]:
    """Create owner-private HOME and TMPDIR replacements (never /dev/null)."""

    root = tempfile.TemporaryDirectory(prefix="yoetz-check-")
    base = Path(root.name)
    home = base / "home"
    tmp = base / "tmp"
    home.mkdir(mode=0o700)
    tmp.mkdir(mode=0o700)
    os.chmod(home, stat.S_IRWXU)
    os.chmod(tmp, stat.S_IRWXU)
    return home, tmp, root


def _sanitized_env(*, home: Path, tmpdir: Path) -> dict[str, str]:
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "PAGER": "cat",
        "TERM": "dumb",
    }


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


def _wait_process(process: subprocess.Popen[bytes], timeout_seconds: float) -> bool:
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


def _posix_process_group_id(process: subprocess.Popen[bytes]) -> int | None:
    get_group = getattr(os, "getpgid", None)
    get_self_group = getattr(os, "getpgrp", None)
    if not callable(get_group) or not callable(get_self_group):
        return None
    try:
        group_id = get_group(process.pid)
        self_group = get_self_group()
    except OSError, ProcessLookupError:
        return None
    if type(group_id) is not int or type(self_group) is not int:
        return None
    if group_id <= 1 or group_id == self_group:
        return None
    return group_id


def _signal_posix_group(group_id: int, sig: int) -> bool:
    kill_group = getattr(os, "killpg", None)
    if not callable(kill_group):
        return False
    try:
        kill_group(group_id, sig)
    except OSError, ProcessLookupError:
        return False
    return True


def _posix_group_exists(group_id: int) -> bool:
    return _signal_posix_group(group_id, 0)


def _reap_approved_check_process(
    process: subprocess.Popen[bytes],
    *,
    process_group_id: int | None = None,
) -> None:
    """Terminate the isolated check tree, then wait until the leader is reaped."""

    if os.name == "nt":
        if process.poll() is None:
            process.kill()
        _wait_process(process, 5.0)
        if process.poll() is None:
            process.kill()
            _wait_process(process, 5.0)
        return

    group_id = (
        process_group_id if process_group_id is not None else _posix_process_group_id(process)
    )
    if group_id is None:
        if process.poll() is None:
            process.kill()
        _wait_process(process, 5.0)
        return

    _signal_posix_group(group_id, signal.SIGTERM)
    if not _wait_process(process, 0.5) or _posix_group_exists(group_id):
        _signal_posix_group(group_id, signal.SIGKILL)
        _wait_process(process, 5.0)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _posix_group_exists(group_id):
        _signal_posix_group(group_id, signal.SIGKILL)
        if _wait_process(process, 0.05):
            time.sleep(0.01)
            continue
        time.sleep(0.01)

    if process.poll() is None:
        process.kill()
        _wait_process(process, 1.0)


class ApprovedCheckRunner:
    """Execute only commitment-approved argv under a consented workspace root."""

    def __init__(
        self,
        approvals: Mapping[str, ApprovedCheckApproval] | None = None,
        *,
        sandbox: CheckSandboxPort | None = None,
        output_sink: Callable[[bytes], None] | None = None,
    ) -> None:
        self._approvals = dict(approvals or {})
        self._sandbox = sandbox if sandbox is not None else default_check_sandbox()
        self._output_sink = output_sink

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
        home, tmpdir, temp_root = _owner_private_temp_dirs()
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        process_group_id: int | None = None
        try:
            env = _sanitized_env(home=home, tmpdir=tmpdir)
            launch = self._sandbox.prepare(
                argv=approval.argv,
                cwd=root,
                env=env,
                deny_network=True,
            )
            if launch.status is CheckSandboxStatus.UNAVAILABLE or not launch.network_isolated:
                # Never claim network denial from an environment variable alone.
                return self._result(
                    ApprovedCheckStatus.REJECTED,
                    ApprovedCheckOutcome.SANDBOX_UNAVAILABLE,
                    None,
                    None,
                    0,
                    command.subject_state_digest,
                    approval.approval_commitment,
                    int((time.monotonic() - started) * 1000),
                )
            if os.name == "nt":
                process = subprocess.Popen(
                    list(launch.argv),
                    cwd=launch.cwd,
                    env=dict(launch.env),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    creationflags=int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)),
                )
            else:
                process = subprocess.Popen(
                    list(launch.argv),
                    cwd=launch.cwd,
                    env=dict(launch.env),
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                    start_new_session=True,
                )
                # ``start_new_session`` makes the child's PID the new session and process-group
                # ID.  Capture that identity without a post-launch ``getpgid`` lookup: the group
                # can remain alive after a short-lived leader exits, and losing the leader must
                # not make its descendants unaddressable at timeout.
                process_group_id = process.pid
            stdout, truncated = _bounded_communicate(
                process,
                stdout_limit=_MAX_OUTPUT_BYTES,
                timeout_seconds=approval.timeout_seconds,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            exit_status = int(process.returncode or 0)
            safe_output, _redacted = redact_sensitive_content(bytes(stdout))
            captured_len = len(safe_output)
            output_digest = "sha256:" + hashlib.sha256(safe_output).hexdigest()
            if self._output_sink is not None:
                # Ephemeral handoff to the ready-service encrypted object writer.
                # The sink receives only the redacted bounded buffer.
                self._output_sink(safe_output)
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
                _reap_approved_check_process(process, process_group_id=process_group_id)
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
            temp_root.cleanup()

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
