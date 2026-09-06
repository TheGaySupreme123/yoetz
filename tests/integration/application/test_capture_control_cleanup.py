"""Runtime admission cleanup for durable native capture handoffs."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from integration.application.test_check import (
    _App as _CheckApp,  # pyright: ignore[reportPrivateUsage]
)
from integration.application.test_check import (
    _request as _check_request,  # pyright: ignore[reportPrivateUsage]
)
from integration.application.test_check import (
    execute_check_commit as _execute_check_commit,  # pyright: ignore[reportPrivateUsage]
)
from integration.application.test_native_capture_pipeline import (
    _ZERO_DIGEST,  # pyright: ignore[reportPrivateUsage]
    _capture_claude_post_requests,  # pyright: ignore[reportPrivateUsage]
    _claude_hook_runner,  # pyright: ignore[reportPrivateUsage]
    _ids,  # pyright: ignore[reportPrivateUsage]
    _pipeline,  # pyright: ignore[reportPrivateUsage]
)
from yoetz.application.observation_drain import ObservationOutboxSweeper
from yoetz.application.observation_materialize import observation_content_identity
from yoetz.domain.observation import (
    OBSERVATION_CONTENT_CAPTURE_PENDING_REASON,
    ObservationIngestDisposition,
)
from yoetz.domain.observation_profiles import CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
from yoetz.ports.ledger import FrozenCase
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind
from yoetz.service import ready_composition


@pytest.mark.anyio
async def test_disabled_runtime_sweeper_revokes_native_ticket_before_successor_freeze(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A READY replay retires the exact native handoff before returning disabled."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    (
        project,
        workspace,
        _session_commitment,
        local,
        observation,
        ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(tmp_path, codex_session_id="claude:disabled-sweep", profile=profile)
    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )

    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "disabled-sweep",
                "tool_name": "Bash",
                "tool_use_id": "disabled-sweep-tool",
            },
        )
        == 0
    )
    captured = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="disabled-sweep",
        tool_use_id="disabled-sweep-tool",
        marker=b"disabled-runtime-marker",
    )
    capture = next(item for item in captured if item.capture_only)
    structural = next(item for item in captured if not item.capture_only)
    staged = await coordinator.ingest_request(capture)
    assert staged.disposition is ObservationIngestDisposition.REJECTED
    assert staged.reason == OBSERVATION_CONTENT_CAPTURE_PENDING_REASON
    logical_identity = observation_content_identity(structural.envelope)
    pending = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=logical_identity,
    )
    assert pending is not None and pending.state == "pending"

    # The structural arm is the durable local outbox row that READY will replay.
    local.enqueue_outbox(workspace, "claude:disabled-sweep", structural.envelope)
    coordinator.observation_enabled = False
    summary = await ObservationOutboxSweeper(local, coordinator).sweep()
    assert summary.attempted == 1
    assert summary.retry_pending == 1

    retired = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=logical_identity,
    )
    assert retired is not None and retired.state == "revoked"
    frontier = await ledger.load_frontier()
    frozen = await ledger.freeze_case(
        runtime.session_id,
        cast(str, runtime.writer_id),
        frontier.sequence,
        _ids(IdKind.REQUEST, 9_801),
        _ZERO_DIGEST,
    )
    assert isinstance(frozen, FrozenCase)


@pytest.mark.anyio
async def test_disabled_runtime_does_not_cleanup_wrong_session_commitment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admission cleanup never turns an invalid session request into authority."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    (
        project,
        workspace,
        _session_commitment,
        _local,
        observation,
        _ledger,
        _runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(tmp_path, codex_session_id="claude:disabled-auth", profile=profile)
    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )
    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "disabled-auth",
                "tool_name": "Bash",
                "tool_use_id": "disabled-auth-tool",
            },
        )
        == 0
    )
    captured = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="disabled-auth",
        tool_use_id="disabled-auth-tool",
        marker=b"disabled-auth-marker",
    )
    capture = next(item for item in captured if item.capture_only)
    staged = await coordinator.ingest_request(capture)
    assert staged.reason == OBSERVATION_CONTENT_CAPTURE_PENDING_REASON
    logical_identity = observation_content_identity(capture.envelope)
    coordinator.observation_enabled = False

    invalid = replace(
        capture,
        envelope=replace(
            capture.envelope,
            session_commitment="hmac-sha256:" + "f" * 64,
        ),
    )
    rejected = await coordinator.ingest_request(invalid)
    assert rejected.reason == "consent_missing"
    still_pending = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=logical_identity,
    )
    assert still_pending is not None and still_pending.state == "pending"


@pytest.mark.anyio
async def test_check_preflight_retires_disabled_ticket_without_structural_outbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task-local CHECK preflight closes a ticket after a public local disable."""

    profile = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
    (
        project,
        workspace,
        _session_commitment,
        local,
        observation,
        ledger,
        runtime,
        coordinator,
        client,
        connect,
    ) = await _pipeline(tmp_path, codex_session_id="claude:disabled-check", profile=profile)
    run_hook = _claude_hook_runner(
        project=project,
        state=tmp_path / "state",
        connect=connect,
        profile=profile,
    )
    assert (
        await asyncio.to_thread(
            run_hook,
            "PreToolUse",
            {
                "hook_event_name": "PreToolUse",
                "session_id": "disabled-check",
                "tool_name": "Bash",
                "tool_use_id": "disabled-check-tool",
            },
        )
        == 0
    )
    captured = await _capture_claude_post_requests(
        client=client,
        monkeypatch=monkeypatch,
        run_hook=run_hook,
        session_id="disabled-check",
        tool_use_id="disabled-check-tool",
        marker=b"disabled-check-marker",
    )
    capture = next(item for item in captured if item.capture_only)
    staged = await coordinator.ingest_request(capture)
    assert staged.reason == OBSERVATION_CONTENT_CAPTURE_PENDING_REASON
    logical_identity = observation_content_identity(capture.envelope)
    local.disable_content_capture(workspace, profile)
    # The hook helper deliberately exercised the structural delivery attempt as well. Remove
    # those delivery rows through the public acknowledgement API so this fixture isolates the
    # durable ticket with no structural outbox successor.
    pending_rows = local.list_pending_outbox_rows(
        workspace,
        codex_session_id="claude:disabled-check",
    )
    for row in pending_rows:
        assert local.acknowledge_outbox_row(workspace, row)
    assert local.pending_outbox_count(workspace) == 0
    pending = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=logical_identity,
    )
    assert pending is not None and pending.state == "pending"

    reconcile = getattr(ready_composition, "_reconcile_observation_capture")
    await reconcile(runtime, local)
    retired = observation.load_capture_ticket(
        workspace=workspace,
        logical_identity=logical_identity,
    )
    assert retired is not None and retired.state == "revoked"
    frontier = await ledger.load_frontier()
    frozen = await ledger.freeze_case(
        runtime.session_id,
        cast(str, runtime.writer_id),
        frontier.sequence,
        _ids(IdKind.REQUEST, 9_802),
        _ZERO_DIGEST,
    )
    assert isinstance(frozen, FrozenCase)


@pytest.mark.anyio
async def test_check_reconciles_once_after_capture_barrier_before_retrying_freeze() -> None:
    """A new CHECK retries its exact freeze once after the bounded capture fence runs."""

    app = _CheckApp()
    original_freeze = app.ledger.freeze_case
    first = True
    calls: list[object] = []

    async def freeze_once(*args: object) -> object:
        nonlocal first
        if first:
            first = False
            raise PublicOperationError(
                PublicErrorCode.OPERATION_PENDING,
                "capture pending",
                True,
            )
        return await original_freeze(*args)

    async def reconcile(runtime: object) -> None:
        assert app.ledger.operation is None
        calls.append(runtime)

    app.ledger.freeze_case = freeze_once  # type: ignore[method-assign]
    setattr(app, "reconcile_observation_capture", reconcile)
    result = await _execute_check_commit(app, _check_request())

    assert result.outcome == "committed"
    assert len(calls) == 1


@pytest.mark.anyio
async def test_completed_check_replay_skips_capture_reconciliation() -> None:
    """An idempotent replay returns before inspecting newer capture tickets."""

    app = _CheckApp()
    first = await _execute_check_commit(app, _check_request())

    async def fail_if_called(_runtime: object) -> None:
        raise AssertionError("capture reconciliation must not run for a completed replay")

    setattr(app, "reconcile_observation_capture", fail_if_called)
    app.ledger.replay = first
    replayed = await _execute_check_commit(app, _check_request())

    assert replayed is first
