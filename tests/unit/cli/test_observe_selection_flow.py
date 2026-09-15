"""Actual native ingress proves selection precedes optional content extraction."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest

from yoetz.adapters.integrations.codex_lifecycle import LifecycleMapping, store_mapping
from yoetz.adapters.integrations.observation_admission import build_routine_read_summary
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.observation_drain import ObservationOutboxSweeper
from yoetz.application.observation_materialize import materialize_observation_envelope
from yoetz.cli import observe_hooks
from yoetz.cli.observe import _selection_preview_plan
from yoetz.domain.observation import (
    ObservationEnvelope,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.domain.observation_budget import ObservationMode
from yoetz.domain.observation_settings import ObservationSelection
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.protocol.canonical import JsonValue, canonical_encode

TASK = "tsk_17607d01-2f55-4b28-82b6-8659242a1267"
SESSION = "ses_6201a23d-a03a-46a5-bda9-d16a7a261ee0"
WRITER = "wri_45c1a9bb-20ac-41ce-9fb9-19394f76e1e1"
HOST = "codex-observation-selection-test"


def setup_store(tmp_path: Path) -> tuple[LocalObservationStore, Path, Path, str, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    root = tmp_path / "isolated"
    store = LocalObservationStore(_state=root)
    commitment = store.workspace_commitment(str(workspace.resolve()))
    store.grant_consent(commitment)
    session = store.bind_codex_session(commitment, HOST)
    store_mapping(LifecycleMapping(1, HOST, TASK, SESSION, WRITER, None), _state=root)
    return store, root, workspace, commitment, session


def hook(
    root: Path,
    workspace: Path,
    phase: str,
    call: str,
    *,
    success: bool = True,
    event_ordinal: int | None = None,
) -> None:
    payload: dict[str, JsonValue] = {
        "session_id": HOST,
        "hook_event_name": phase,
        "tool_name": "Read",
        "tool_use_id": call,
        "tool_input": {"file_path": "public-example.txt"},
    }
    if phase == "PostToolUse":
        payload.update(
            success=success,
            exit_status=0 if success else 1,
            tool_response={"text": "public synthetic output"},
        )
    if event_ordinal is not None:
        payload["event_ordinal"] = event_ordinal
    assert (
        observe_hooks.handle_observe(
            event_name=phase,
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
            workspace=str(workspace),
            _state=root,
            skip_service=True,
        )
        == 0
    )


def test_focused_pair_skips_capture_and_delivers_a_bounded_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root, workspace, commitment, _ = setup_store(tmp_path)
    captures: list[str] = []

    def capture(phase: str, *args: object, **kwargs: object):
        captures.append(phase)
        return (), False

    monkeypatch.setattr(observe_hooks, "_visible_content_chunks", capture)
    hook(root, workspace, "PreToolUse", "read1")
    hook(root, workspace, "PostToolUse", "read1")
    assert captures == []
    assert store.selection_accounting(commitment)["buffered_input_count"] == 2
    # The cursor is the authoritative accepted-input account after selected
    # routine diagnostics are reclaimed from the secondary envelope cache.
    assert store.list_envelopes(commitment) == ()
    assert store.status(ObservationStatusQuery(commitment)).source_coverage[
        ObservationSource.CODEX_HOOK
    ]
    assert store.flush_selected_admission(
        commitment, summary_builder=build_routine_read_summary, force=True
    )
    rows = store.list_pending_outbox_rows(commitment)
    assert len(rows) == 1
    assert rows[0].envelope.event_kind == "RoutineReadSummary"
    assert rows[0].envelope.structural_payload["input_count"] == 2
    batch = materialize_observation_envelope(rows[0].envelope, task_id=TASK)
    assert len(batch.drafts) == 1
    assert "routine_read_detail_omitted" in batch.coverage.known_gaps


def test_negative_read_keeps_original_attempt_outcome_and_capture_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root, workspace, commitment, _ = setup_store(tmp_path)
    captures: list[str] = []

    def capture(phase: str, *args: object, **kwargs: object):
        captures.append(phase)
        return (), False

    monkeypatch.setattr(observe_hooks, "_visible_content_chunks", capture)
    hook(root, workspace, "PreToolUse", "negative-read")
    hook(root, workspace, "PostToolUse", "negative-read", success=False)
    rows = store.list_pending_outbox_rows(commitment)
    assert [row.envelope.event_kind for row in rows] == ["PreToolUse", "PostToolUse"]
    assert rows[-1].envelope.structural_payload["exit_status"] == 1
    assert rows[-1].envelope.structural_payload["success"] is False
    assert captures == ["PostToolUse"]


def test_detailed_keeps_individual_records_without_increasing_content_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root, workspace, commitment, session = setup_store(tmp_path)
    authority = store.content_capture_authority(commitment)
    store.set_session_selection(
        commitment, session, ObservationSelection(detail=ObservationMode.DETAILED)
    )
    captures: list[str] = []

    def capture(phase: str, *args: object, **kwargs: object):
        captures.append(phase)
        return (), False

    monkeypatch.setattr(observe_hooks, "_visible_content_chunks", capture)
    hook(root, workspace, "PreToolUse", "detailed-read")
    hook(root, workspace, "PostToolUse", "detailed-read")
    rows = store.list_pending_outbox_rows(commitment)
    assert len(rows) == 2
    assert all(row.envelope.structural_payload["action"] == "routine_read_detailed" for row in rows)
    assert captures == ["PreToolUse", "PostToolUse"]
    assert store.content_capture_authority(commitment) == authority


def test_live_pressure_does_not_invalidate_exact_owner_selection_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root, workspace, commitment, session = setup_store(tmp_path)
    del root, workspace
    selection = ObservationSelection(detail=ObservationMode.DETAILED)
    expiry = Timestamp("2026-09-11T00:00:00.000Z")
    monkeypatch.setattr(store, "_wall_timestamp", lambda: Timestamp("2026-09-10T00:01:00.000Z"))
    first_plan, first_digest = _selection_preview_plan(
        store,
        commitment,
        selection,
        session_commitment=session,
        scope="session",
        expires_at=expiry,
    )
    store.update_capture_backlog(
        commitment,
        450,
        0,
        Timestamp("2026-09-10T00:00:00.000Z"),
        Timestamp("2026-09-10T00:01:00.000Z"),
        route_id="capture-live",
    )
    second_plan, second_digest = _selection_preview_plan(
        store,
        commitment,
        selection,
        session_commitment=session,
        scope="session",
        expires_at=expiry,
    )
    assert first_digest == second_digest
    assert first_plan["pressure_state"] != second_plan["pressure_state"]
    assert first_plan["accounting"] == second_plan["accounting"]


def test_buffered_promotion_retains_native_identity_and_reports_missing_content(
    tmp_path: Path,
) -> None:
    store, root, workspace, commitment, _ = setup_store(tmp_path)
    hook(root, workspace, "PreToolUse", "promoted-read")
    hook(root, workspace, "PostToolUse", "promoted-read")
    buffered = store._load(commitment).admission_buffer.inputs
    original = buffered[-1].envelope
    result = store.promote_buffered_observation(commitment, original.source_identity)
    assert result["outcome"] == "queued_individual"
    assert result["content_availability"] == "not_retained"
    rows = store.list_pending_outbox_rows(commitment)
    assert rows[-1].envelope.source_identity == original.source_identity
    assert rows[-1].envelope.cursor == original.cursor
    assert rows[-1].envelope.structural_payload["action"] == "evidence_linked_read"
    assert (
        store.promote_buffered_observation(commitment, original.source_identity)["reason"]
        == "promotion_window_closed"
    )


def test_loss_history_survives_recovery_without_retargeting_another_task(tmp_path: Path) -> None:
    store, root, workspace, commitment, _ = setup_store(tmp_path)
    hook(root, workspace, "PreToolUse", "original")
    hook(root, workspace, "PostToolUse", "original")
    original = store._load(commitment).admission_buffer.inputs[-1].envelope
    lost = replace(original, source_identity="native:lost-input")
    assert store.record_admission_loss(commitment, lost)
    assert not store.record_admission_loss(commitment, lost)
    assert store.flush_selected_admission(
        commitment, summary_builder=build_routine_read_summary, force=True
    )
    for row in store.list_pending_outbox_rows(commitment):
        assert store.acknowledge_outbox_row(commitment, row)
    reopened = LocalObservationStore(_state=root)
    assert reopened.selection_history_gaps(commitment, original) == ("observation_input_loss",)
    different_task = replace(
        original,
        structural_payload=JsonObject(
            {
                **original.structural_payload,
                "selection_task_id": "tsk_00000000-0000-4000-8000-000000000001",
            }
        ),
    )
    assert reopened.selection_history_gaps(commitment, different_task) == ()
    hook(root, workspace, "PreToolUse", "after-recovery")
    hook(root, workspace, "PostToolUse", "after-recovery")
    assert reopened.flush_selected_admission(
        commitment, summary_builder=build_routine_read_summary, force=True
    )
    rows = reopened.list_pending_outbox_rows(commitment)
    assert rows[-1].envelope.event_kind == "RoutineReadSummary"
    batch = materialize_observation_envelope(rows[-1].envelope, task_id=TASK)
    assert "observation_input_loss" in batch.coverage.known_gaps


def test_selected_admission_exception_rolls_back_ingest_for_exact_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed selected commit cannot leave a duplicate-only local envelope behind."""

    _store, root, workspace, commitment, _ = setup_store(tmp_path)
    original_commit = LocalObservationStore.commit_selected_admission
    calls = 0

    def fail_once(
        selected_store: LocalObservationStore,
        selected_workspace: str,
        plan: object,
        **kwargs: object,
    ) -> bool:
        nonlocal calls
        if calls == 0:
            calls += 1
            raise RuntimeError("selected_commit_fault")
        return original_commit(  # type: ignore[arg-type]
            selected_store,
            selected_workspace,
            plan,  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(LocalObservationStore, "commit_selected_admission", fail_once)
    hook(root, workspace, "PreToolUse", "atomic-retry")
    assert LocalObservationStore(_state=root).list_envelopes(commitment) == ()

    hook(root, workspace, "PreToolUse", "atomic-retry")
    hook(root, workspace, "PostToolUse", "atomic-retry")
    reopened = LocalObservationStore(_state=root)
    assert reopened.list_envelopes(commitment) == ()
    assert len(reopened._load(commitment).admission_buffer.inputs) == 2
    assert reopened.flush_selected_admission(
        commitment, summary_builder=build_routine_read_summary, force=True
    )
    rows = reopened.list_pending_outbox_rows(commitment)
    assert [row.envelope.event_kind for row in rows] == ["RoutineReadSummary"]
    assert rows[0].envelope.structural_payload["input_count"] == 2


@pytest.mark.anyio
async def test_fresh_background_sweeper_delivers_buffer_without_another_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root, workspace, commitment, _ = setup_store(tmp_path)

    # Keep the four synthetic hook calls within one admission deadline even
    # when the isolated hook subprocess setup is slow on the test host.
    def fixed_wall_now(_store: LocalObservationStore) -> float:
        return 1_000_000.0

    monkeypatch.setattr(LocalObservationStore, "_wall_now", fixed_wall_now)
    # A host retry can replay the same source identity and cursor.  It must be
    # idempotent in both the observed/admitted counters and the buffered pair.
    hook(root, workspace, "PreToolUse", "before-restart", event_ordinal=1)
    hook(root, workspace, "PostToolUse", "before-restart", event_ordinal=2)
    hook(root, workspace, "PreToolUse", "before-restart", event_ordinal=1)
    hook(root, workspace, "PostToolUse", "before-restart", event_ordinal=2)
    assert store.list_pending_outbox_rows(commitment) == ()
    before_sweep = store.selection_accounting(commitment)
    assert before_sweep["observed_count"] == 2
    assert before_sweep["admitted_input_count"] == 2
    assert before_sweep["summarized_input_count"] == 0
    assert before_sweep["delivered_input_count"] == 0
    assert before_sweep["intentionally_omitted_input_count"] == 0
    received: list[ObservationEnvelope] = []

    class Coordinator:
        async def ingest_request(
            self, request: ObservationIngestRequest
        ) -> ObservationIngestResult:
            received.append(request.envelope)
            return ObservationIngestResult(
                ObservationIngestDisposition.ACCEPTED, None, request.envelope.cursor
            )

    reopened = LocalObservationStore(_state=root)
    sweeper = ObservationOutboxSweeper(reopened, Coordinator())
    result = await sweeper.sweep()
    assert result.acknowledged == 1
    assert len(received) == 1
    assert received[0].event_kind == "RoutineReadSummary"
    assert received[0].structural_payload["input_count"] == 2
    assert reopened.selection_accounting(commitment)["buffered_input_count"] == 0
    assert reopened.list_pending_outbox_rows(commitment) == ()
    after_sweep = reopened.selection_accounting(commitment)
    assert after_sweep["observed_count"] == 2
    assert after_sweep["admitted_input_count"] == 2
    assert after_sweep["summarized_input_count"] == 2
    assert after_sweep["delivered_input_count"] == 2
    assert after_sweep["intentionally_omitted_input_count"] == 0

    # A second sweep sees no row and cannot account the same summary twice.
    second = await sweeper.sweep()
    assert second.attempted == 0
    assert reopened.selection_accounting(commitment) == after_sweep
    sweeper.close()
