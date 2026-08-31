"""Agent-authorized vault initialization over the real client/service composition (#510).

The defect this locks against: `yoetz consent authorize` completed the real human-control
ceremony, received a non-ready `VaultStateResult`, and still consumed the approval before the
result was validated — collapsing the actionable service reason to `authorize_failed` while the
audit said approved. These tests run real agent attestation, a generated local secret, the real
YZH1/YZS1 client and service transport, and a real unlock throttle, mocking only the OS keyring.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

import yoetz.service.daemon as daemon_module
from yoetz.adapters.control.unix_socket import (
    bind_control_listener,
    bind_human_control_listener,
    bind_secret_listener,
)
from yoetz.cli import elevated
from yoetz.config.load import YoetzConfig
from yoetz.service.daemon import ServiceDaemon
from yoetz.service.elevated_bootstrap import ElevatedBootstrapError, load_pending
from yoetz.service.unlock import UnlockThrottleRecord

_FOREIGN_INSTALLATION_ID = "ins_30000000-0000-4000-8000-000000000001"
_FOREIGN_WRITER_ID = "svc_30000000-0000-4000-8000-000000000002"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def runtime_directory(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Short in-tree socket directory: AF_UNIX paths bound under tmp_path are too long."""

    runtime = Path(tempfile.mkdtemp(prefix=".yctl-", dir=Path.cwd()))
    runtime.chmod(0o700)
    monkeypatch.setattr(
        "yoetz.adapters.control.unix_socket._runtime_directory",
        lambda: runtime,
    )
    try:
        yield runtime
    finally:
        shutil.rmtree(runtime, ignore_errors=True)


def _stage_non_pristine_throttle_record(path: Path) -> None:
    """The exact repro state (#510): uninitialized bundle, non-pristine per-user throttle."""

    import os

    record = UnlockThrottleRecord.create(
        installation_id=_FOREIGN_INSTALLATION_ID,
        record_generation=1,
        consecutive_failures=1,
        attempt_in_progress=False,
        last_failure_utc="2026-08-31T11:00:00.000Z",
        last_writer_instance_id=_FOREIGN_WRITER_ID,
    )
    path.write_bytes(record.encode())
    os.chmod(path, 0o600)


async def _production_daemon(tmp_path: Path, *, pristine: bool) -> ServiceDaemon:
    root = tmp_path / "data"
    metadata = tmp_path / "state"
    root.mkdir(mode=0o700, exist_ok=True)
    metadata.mkdir(mode=0o700, exist_ok=True)
    if not pristine:
        _stage_non_pristine_throttle_record(metadata / "unlock-throttle.json")
    paths = daemon_module._ProductionPaths(  # pyright: ignore[reportPrivateUsage]
        root,
        metadata / "service-generation.json",
        metadata / "unlock-throttle.json",
        metadata / "service.lock",
        metadata,
    )
    binders = daemon_module._ListenerBinders(  # pyright: ignore[reportPrivateUsage]
        bind_control_listener,
        bind_secret_listener,
        bind_human_control_listener,
    )
    composition = await daemon_module._production_composition(  # pyright: ignore[reportPrivateUsage]
        _config=YoetzConfig(),
        _paths=paths,
        _binders=binders,
    )
    return ServiceDaemon(_composition=composition)


def _assert_no_secret_bytes(tree: Path, secret: bytes) -> None:
    for path in tree.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert secret not in path.read_bytes(), path


@pytest.mark.anyio
async def test_agent_authorized_initialize_non_ready_result_is_bounded_and_never_approved(
    tmp_path: Path,
    runtime_directory: Path,
) -> None:
    tmp_path.chmod(0o700)
    consent_state = tmp_path / "consent"
    consent_state.mkdir(mode=0o700)
    generated = bytearray(b"g" * 64)
    generated_copy = bytes(generated)

    class _Store:
        def create_for_initialization(self) -> bytearray:
            return generated

    daemon = await _production_daemon(tmp_path, pristine=False)
    # Publish the endpoints before any client runs; serve() then only arms the accept loops.
    await daemon.start()
    serving = asyncio.create_task(daemon.serve())
    try:
        with (
            patch("yoetz.service.elevated_bootstrap.state_dir", return_value=consent_state),
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=_Store()),
        ):
            elevated.prepare_elevated("vault_initialize")
            pending = load_pending(_state=consent_state)
            assert pending is not None
            attestation = {
                "schema": "yoetz.chat-user-attestation/1",
                "channel": "agent_attested_chat_instruction",
                "client_kind": "codex",
                "instruction_source": "explicit_current_chat_user",
                "pending_id": pending.pending_id,
                "operation": pending.operation,
                "danger_digest": pending.danger_digest,
                "target_digest": pending.target_digest,
                "warning_acknowledged": True,
                "decision": "approve",
            }
            with pytest.raises(ElevatedBootstrapError) as exc:
                await asyncio.wait_for(elevated.authorize_elevated(attestation), timeout=30)

            # The actionable service reason survives as a bounded token, never the generic
            # authorize_failed collapse.
            assert exc.value.reason == "vault_result_throttle_record_exists"
            # The approval is consumed as failed with a stable correlation; nothing durable
            # claims success and the audit never says approved.
            assert load_pending(_state=consent_state) is None
            audit_lines = (
                (consent_state / "elevated-bootstrap" / "elevated-bootstrap-audit.jsonl")
                .read_text("utf-8")
                .splitlines()
            )
            assert not any('"outcome":"approved"' in line for line in audit_lines)
            consumed = [line for line in audit_lines if '"event":"review_consumed"' in line]
            assert len(consumed) == 1
            assert '"outcome":"failed"' in consumed[0]
            assert '"failure_reason":"vault_result_throttle_record_exists"' in consumed[0]
            assert pending.pending_id in consumed[0]

            # The daemon still reports the truthful vault state.
            assert daemon.status().vault_mode == "uninitialized"
            assert not (tmp_path / "data" / "vault").exists()
    finally:
        await daemon.stop()
        await asyncio.wait_for(serving, timeout=10)

    # The generated local secret was wiped and never persisted to any artifact.
    assert bytes(generated) == b"\x00" * len(generated)
    _assert_no_secret_bytes(tmp_path, generated_copy)
    _assert_no_secret_bytes(runtime_directory, generated_copy)


@pytest.mark.anyio
async def test_agent_authorized_initialize_succeeds_on_pristine_state_without_secret_exposure(
    tmp_path: Path,
    runtime_directory: Path,
) -> None:
    tmp_path.chmod(0o700)
    consent_state = tmp_path / "consent"
    consent_state.mkdir(mode=0o700)
    generated = bytearray(b"s" * 64)
    generated_copy = bytes(generated)

    class _Store:
        def create_for_initialization(self) -> bytearray:
            return generated

    daemon = await _production_daemon(tmp_path, pristine=True)
    await daemon.start()
    serving = asyncio.create_task(daemon.serve())
    try:
        with (
            patch("yoetz.service.elevated_bootstrap.state_dir", return_value=consent_state),
            patch("yoetz.cli.elevated._auto_unlock_store", return_value=_Store()),
        ):
            elevated.prepare_elevated("vault_initialize")
            pending = load_pending(_state=consent_state)
            assert pending is not None
            attestation = {
                "schema": "yoetz.chat-user-attestation/1",
                "channel": "agent_attested_chat_instruction",
                "client_kind": "codex",
                "instruction_source": "explicit_current_chat_user",
                "pending_id": pending.pending_id,
                "operation": pending.operation,
                "danger_digest": pending.danger_digest,
                "target_digest": pending.target_digest,
                "warning_acknowledged": True,
                "decision": "approve",
            }
            result = await asyncio.wait_for(elevated.authorize_elevated(attestation), timeout=30)

            assert result["outcome"] == "completed"
            assert result["result"] == {"state": "ready", "reason": "succeeded"}
            assert load_pending(_state=consent_state) is None
            audit_lines = (
                (consent_state / "elevated-bootstrap" / "elevated-bootstrap-audit.jsonl")
                .read_text("utf-8")
                .splitlines()
            )
            assert any('"outcome":"approved"' in line for line in audit_lines)
            assert not any('"outcome":"failed"' in line for line in audit_lines)
            assert daemon.status().vault_mode == "passphrase"
    finally:
        await daemon.stop()
        await asyncio.wait_for(serving, timeout=10)

    assert bytes(generated) == b"\x00" * len(generated)
    _assert_no_secret_bytes(tmp_path, generated_copy)
    _assert_no_secret_bytes(runtime_directory, generated_copy)
