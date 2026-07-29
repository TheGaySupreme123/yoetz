from __future__ import annotations

import errno
import fcntl
import json
import os
import pty
import select
import subprocess
import sys
import termios
import time
from pathlib import Path
from typing import cast

import pytest

_PREVIEW_CANARY = b"privacy-preview-sensitive-canary"
_SECRET_CANARY = b"policy-secret-canary-2026"

_TTY_PROBE = r"""
import asyncio, json, os, sys, time
from yoetz.cli import privacy_control
from yoetz.cli import unlock as unlock_helper
from yoetz.protocol.canonical import canonical_digest
from yoetz.service.confidential_protocol import (
    AuthorizationRequiredPhase, DecisionAction, DecisionRequiredPhase,
    HumanCeremonyBinding, HumanCeremonyKind, PrivacyDecisionResult,
    PrivacyDisclosureDecisionPreview, PrivacyPendingTarget,
    PrivacyPolicyDecisionPreview, SecretIngressBinding, SecretRequiredPhase,
    SelectAuthorizationSourceAction, ConfidentialSecretPurpose, ServerOpenedEnvelope,
)

MODE = sys.argv[1]
AUTO_SECRET = b"policy-secret-canary-2026"
REAL_OPEN = os.open
def OPEN_TTY(path, flags):
    assert path == "/dev/tty"
    return os.dup(0)
unlock_helper.os.open = OPEN_TTY
POLICY_MODE = MODE in {"policy", "auto_policy"}
TARGET = PrivacyPendingTarget("policy" if POLICY_MODE else "disclosure", "pending-1")
TARGET_DIGEST = canonical_digest({"decision_kind": TARGET.decision_kind, "kind": TARGET.kind, "pending_id": TARGET.pending_id})
CEREMONY = "1" * 64
INSTANCE = "svc_11111111-1111-4111-8111-111111111111"
ACTIONS = []
SECRET_LENGTHS = []

class SecretClient:
    def __init__(self, session): self.session = session
    async def send_once(self, binding, source, token):
        assert token is self.session.token
        assert binding == self.session.secret_binding
        SECRET_LENGTHS.append(len(source))
        source[:] = b"\x00" * len(source)
        self.session.stage = 3

class Session:
    def __init__(self, kind):
        self.stage = 0
        self.token = object()
        self.secret_binding = SecretIngressBinding(
            1, CEREMONY, "2" * 64, ConfidentialSecretPurpose.PRIVACY_REAUTHENTICATION,
            INSTANCE, 7, 3, 5, TARGET_DIGEST, int(time.monotonic() * 1000) + 60000,
        )
        binding = HumanCeremonyBinding(
            1, CEREMONY, "3" * 64, kind, INSTANCE, 7, 3, 5, TARGET_DIGEST,
            int(time.monotonic() * 1000) + 60000,
        )
        if POLICY_MODE:
            preview = PrivacyPolicyDecisionPreview(
                "pending-1", "sha256:" + "4" * 64,
                ("source-content",), ("workspace",),
            )
        else:
            preview = PrivacyDisclosureDecisionPreview(
                "pending-1", "privacy-preview-sensitive-canary",
                "sha256:" + "5" * 64, "source-content", "sha256:" + "6" * 64,
                36, 5, "sha256:" + "7" * 64,
            )
        self.opened = ServerOpenedEnvelope(CEREMONY, 1, binding, preview, DecisionRequiredPhase())
    async def send_action(self, action):
        if isinstance(action, DecisionAction):
            ACTIONS.append(action.decision)
            self.stage = 1
        elif isinstance(action, SelectAuthorizationSourceAction):
            ACTIONS.append(action.source)
            self.stage = 2
        else: raise AssertionError(type(action).__name__)
    async def wait_phase_or_result(self):
        if not POLICY_MODE:
            return PrivacyDecisionResult("committed", "sha256:" + "8" * 64)
        if self.stage == 1:
            return AuthorizationRequiredPhase(("secret_reauthentication",))
        if self.stage == 2:
            return SecretRequiredPhase(self.secret_binding)
        if self.stage == 3:
            return PrivacyDecisionResult("committed", "sha256:" + "8" * 64)
        raise AssertionError(self.stage)
    async def cancel(self): ACTIONS.append("cancel")
    async def close(self): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *args): await self.close()
    def _secret_client(self): return SecretClient(self)
    def _session_token(self): return self.token

class Client:
    async def open(self, kind, target):
        assert target == TARGET
        return Session(kind)
    async def close(self): pass

privacy_control.HumanControlClient = Client

async def main():
    result = await (
        (
            privacy_control.decide_policy_with_local_reauthentication(
                "pending-1", bytearray(AUTO_SECRET)
            )
            if MODE == "auto_policy"
            else privacy_control.decide_policy("pending-1")
        )
        if POLICY_MODE
        else privacy_control.decide_disclosure("pending-1")
    )
    print(json.dumps({
        "actions": ACTIONS,
        "result": getattr(result, "outcome", getattr(result, "status", None)),
        "secret_lengths": SECRET_LENGTHS,
    }, separators=(",", ":"), sort_keys=True))

asyncio.run(main())
"""

_NO_TTY_PROBE = r"""
import asyncio, json
from yoetz.cli import privacy_control

opened = False
class Client:
    async def open(self, kind, target):
        global opened
        opened = True
        raise AssertionError("ceremony opened without tty")
    async def close(self): pass
privacy_control.HumanControlClient = Client

async def main():
    try:
        await privacy_control.decide_disclosure("pending-1")
        reason = "unexpected_success"
    except Exception as exc:
        reason = getattr(exc, "reason", type(exc).__name__)
    print(json.dumps({"opened": opened, "reason": reason}, separators=(",", ":"), sort_keys=True))
asyncio.run(main())
"""


def _base_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "VIRTUAL_ENV": sys.prefix,
    }


def _read_until(master_fd: int, transcript: bytearray, marker: bytes, deadline: float) -> None:
    while marker not in transcript:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for terminal marker {marker!r}")
        ready, _, _ = select.select([master_fd], [], [], min(remaining, 0.2))
        if not ready:
            continue
        try:
            chunk = os.read(master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                break
            raise
        if not chunk:
            break
        transcript.extend(chunk)


def _read_remaining(master_fd: int, transcript: bytearray) -> None:
    while True:
        ready, _, _ = select.select([master_fd], [], [], 0.05)
        if not ready:
            return
        try:
            chunk = os.read(master_fd, 4096)
        except OSError as exc:
            if exc.errno == errno.EIO:
                return
            raise
        if not chunk:
            return
        transcript.extend(chunk)


def _spawn_tty_probe(mode: str) -> tuple[subprocess.Popen[bytes], int, int]:
    master_fd, slave_fd = pty.openpty()

    def establish_controlling_tty() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(
        [sys.executable, "-I", "-c", _TTY_PROBE, mode],
        stdin=slave_fd,
        stdout=subprocess.PIPE,
        stderr=slave_fd,
        env=_base_env(),
        close_fds=True,
        preexec_fn=establish_controlling_tty,
    )
    return process, master_fd, slave_fd


def _finish_tty_probe(
    process: subprocess.Popen[bytes],
    master_fd: int,
    slave_fd: int,
    transcript: bytearray,
) -> tuple[dict[str, object], bytes]:
    deadline = time.monotonic() + 10
    while process.poll() is None:
        if time.monotonic() >= deadline:
            process.kill()
            raise AssertionError("privacy TTY probe timed out")
        _read_remaining(master_fd, transcript)
        time.sleep(0.01)
    _read_remaining(master_fd, transcript)
    stdout = process.stdout.read() if process.stdout is not None else b""
    os.close(master_fd)
    os.close(slave_fd)
    assert process.returncode == 0, bytes(transcript).decode("utf-8", errors="replace")
    return cast(dict[str, object], json.loads(stdout)), stdout


@pytest.mark.skipif(not hasattr(termios, "TIOCSCTTY"), reason="requires a POSIX controlling TTY")
def test_disclosure_preview_and_decision_stay_on_tty() -> None:
    process, master_fd, slave_fd = _spawn_tty_probe("disclosure")
    transcript = bytearray()
    deadline = time.monotonic() + 10
    _read_until(master_fd, transcript, b"Decision [approve/deny/edit]: ", deadline)
    os.write(master_fd, b"approve\n")
    observed, stdout = _finish_tty_probe(process, master_fd, slave_fd, transcript)
    assert observed == {"actions": ["approve"], "result": "committed", "secret_lengths": []}
    assert _PREVIEW_CANARY in transcript
    assert _PREVIEW_CANARY not in stdout


@pytest.mark.skipif(not hasattr(termios, "TIOCSCTTY"), reason="requires a POSIX controlling TTY")
def test_policy_approval_reauthenticates_without_echo_or_secret_output() -> None:
    process, master_fd, slave_fd = _spawn_tty_probe("policy")
    transcript = bytearray()
    deadline = time.monotonic() + 10
    _read_until(master_fd, transcript, b"Decision [approve/deny/edit]: ", deadline)
    os.write(master_fd, b"approve\n")
    _read_until(master_fd, transcript, b"Passphrase: ", deadline)
    os.write(master_fd, _SECRET_CANARY + b"\n")
    observed, stdout = _finish_tty_probe(process, master_fd, slave_fd, transcript)
    assert observed == {
        "actions": ["approve", "secret_reauthentication"],
        "result": "committed",
        "secret_lengths": [len(_SECRET_CANARY)],
    }
    assert _SECRET_CANARY not in transcript
    assert _SECRET_CANARY not in stdout


@pytest.mark.skipif(not hasattr(termios, "TIOCSCTTY"), reason="requires a POSIX controlling TTY")
def test_policy_approval_uses_provisioned_reauthentication_without_prompting() -> None:
    process, master_fd, slave_fd = _spawn_tty_probe("auto_policy")
    transcript = bytearray()
    deadline = time.monotonic() + 10
    _read_until(master_fd, transcript, b"Decision [approve/deny/edit]: ", deadline)
    os.write(master_fd, b"approve\n")
    observed, stdout = _finish_tty_probe(process, master_fd, slave_fd, transcript)
    assert observed == {
        "actions": ["approve", "secret_reauthentication"],
        "result": "committed",
        "secret_lengths": [len(_SECRET_CANARY)],
    }
    assert b"Passphrase: " not in transcript
    assert _SECRET_CANARY not in transcript
    assert _SECRET_CANARY not in stdout


@pytest.mark.skipif(not hasattr(termios, "TIOCSCTTY"), reason="requires a POSIX controlling TTY")
def test_edit_is_local_cancel_and_never_a_protocol_decision() -> None:
    process, master_fd, slave_fd = _spawn_tty_probe("edit")
    transcript = bytearray()
    deadline = time.monotonic() + 10
    _read_until(master_fd, transcript, b"Decision [approve/deny/edit]: ", deadline)
    os.write(master_fd, b"edit\n")
    observed, _stdout = _finish_tty_probe(process, master_fd, slave_fd, transcript)
    assert observed == {"actions": ["cancel"], "result": "edit", "secret_lengths": []}
    assert b"action=edit" not in transcript


def test_no_tty_fails_before_opening_human_control() -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _NO_TTY_PROBE],
        input=b"approve\n" + _SECRET_CANARY + b"\n",
        capture_output=True,
        env=_base_env(),
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert json.loads(completed.stdout) == {"opened": False, "reason": "tty_required"}
    assert _SECRET_CANARY not in completed.stdout + completed.stderr
