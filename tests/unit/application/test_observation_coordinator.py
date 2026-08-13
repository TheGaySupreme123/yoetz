"""Focused tests for observation coordinator, materialization, and local outbox."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.codex_lifecycle import LifecycleMapping
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.application.observation_control import build_observation_support_handlers
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.observation_materialize import (
    materialize_observation_envelope,
    observation_writer_id,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationSource,
    ObservationStatusQuery,
    observation_ingest_request_from_json,
    observation_ingest_request_to_json,
)
from yoetz.domain.values import JsonObject, Timestamp, finding_id
from yoetz.ports.runtime import TaskRuntime
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind


def _task_id() -> str:
    return PREFIX_BY_KIND[IdKind.TASK] + str(uuid.uuid4())


def _envelope(
    *,
    session: str,
    kind: str = "PreToolUse",
    identity: str = "hook:test1",
    ordinal: int = 1,
    gaps: tuple[str, ...] = (),
    tool: str = "shell",
    corr: str = "c1",
    exit_status: int | None = None,
) -> ObservationEnvelope:
    structural: dict[str, object] = {
        "tool_name": tool,
        "tool_call_id": corr,
        "correlation_id": corr,
    }
    if exit_status is not None:
        structural["exit_status"] = exit_status
    return ObservationEnvelope(
        session_commitment=session,
        event_kind=kind,
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(1, 0, ordinal, f"hmac-sha256:{'ab' * 32}", "codex-obs-hook/1.0.0"),
        receipt_time=Timestamp("2026-01-01T00:00:00.000Z"),
        structural_payload=JsonObject(structural),
        content_object_refs=(),
        gap_codes=gaps,
    )


def test_observation_ingest_request_round_trip() -> None:
    session = f"hmac-sha256:{'cd' * 32}"
    envelope = _envelope(session=session)
    request = ObservationIngestRequest(codex_session_id="codex-sess-1", envelope=envelope)
    wire = observation_ingest_request_to_json(request)
    assert set(wire) == {"codex_session_id", "envelope"}
    assert "task_id" not in wire
    assert "writer_id" not in wire
    restored = observation_ingest_request_from_json(wire)
    assert restored.codex_session_id == "codex-sess-1"
    assert restored.envelope.source_identity == envelope.source_identity


def test_materialize_pre_post_and_unpaired() -> None:
    task = _task_id()
    session = f"hmac-sha256:{'ef' * 32}"
    pre = materialize_observation_envelope(
        _envelope(session=session, kind="PreToolUse", identity="hook:pre"), task_id=task
    )
    assert pre.skip_reason is None
    assert len(pre.drafts) == 1
    assert pre.drafts[0].draft.schema.name == "action_recorded"

    post = materialize_observation_envelope(
        _envelope(
            session=session,
            kind="PostToolUse",
            identity="hook:post",
            exit_status=1,
        ),
        task_id=task,
    )
    assert post.skip_reason is None
    assert [item.draft.schema.name for item in post.drafts] == [
        "action_recorded",
        "result_recorded",
    ]

    unpaired = materialize_observation_envelope(
        _envelope(
            session=session,
            kind="PostToolUse",
            identity="hook:unpaired",
            gaps=(ObservationGapCode.UNPAIRED_EVENT.value,),
            exit_status=1,
        ),
        task_id=task,
    )
    assert unpaired.skip_reason is None
    assert len(unpaired.drafts) == 1
    assert unpaired.drafts[0].draft.schema.name == "evidence_recorded"
    assert ObservationGapCode.UNPAIRED_EVENT.value in unpaired.gaps


def test_completion_signal_is_evidence_unless_claim_kind_is_explicit() -> None:
    task = _task_id()
    session = f"hmac-sha256:{'ac' * 32}"
    stop = materialize_observation_envelope(
        _envelope(session=session, kind="Stop", identity="hook:stop"), task_id=task
    )
    assert stop.drafts[0].draft.schema.name == "evidence_recorded"
    assert stop.drafts[0].role == "completion_signal"

    envelope = _envelope(session=session, kind="AgentMessage", identity="hook:explicit")
    structural = dict(envelope.structural_payload)
    structural["claim_kind"] = "completion"
    explicit = materialize_observation_envelope(
        ObservationEnvelope(
            session_commitment=envelope.session_commitment,
            event_kind=envelope.event_kind,
            source_identity=envelope.source_identity,
            source=envelope.source,
            cursor=envelope.cursor,
            receipt_time=envelope.receipt_time,
            structural_payload=JsonObject(structural),
            content_object_refs=envelope.content_object_refs,
            gap_codes=envelope.gap_codes,
        ),
        task_id=task,
    )
    assert explicit.drafts[0].draft.schema.name == "claim_recorded"


@pytest.mark.anyio
async def test_live_upgrade_finds_materialization_under_legacy_writer(tmp_path: Path) -> None:
    from types import SimpleNamespace

    task_id = _task_id()
    session_id = PREFIX_BY_KIND[IdKind.SESSION] + str(uuid.uuid4())
    legacy_writer_id = PREFIX_BY_KIND[IdKind.WRITER] + str(uuid.uuid4())
    harness_writer_id = observation_writer_id(task_id, session_id)
    envelope = _envelope(
        session=f"hmac-sha256:{'ae' * 32}", identity="hook:upgrade-hazard"
    )
    batch = materialize_observation_envelope(envelope, task_id=task_id)
    lookups: list[tuple[str, str]] = []

    class _Ledger:
        async def lookup_operation(self, writer_id: str, operation_id: str):
            lookups.append((writer_id, operation_id))
            return object() if writer_id == legacy_writer_id else None

    class _Clock:
        def now_utc(self) -> Timestamp:
            return Timestamp("2026-01-01T00:00:00.000Z")

    class _Ids:
        def new(self, kind: IdKind) -> str:
            return PREFIX_BY_KIND[kind] + str(uuid.uuid4())

    coordinator = ObservationCoordinator(
        runtime=object(),  # type: ignore[arg-type]
        local=LocalObservationStore(_state=tmp_path),
        clock=_Clock(),  # type: ignore[arg-type]
        ids=_Ids(),  # type: ignore[arg-type]
        state_root=tmp_path,
    )
    runtime = SimpleNamespace(
        task_id=task_id,
        session_id=session_id,
        writer_id=harness_writer_id,
        ledger=_Ledger(),
    )

    recovered = await coordinator._append_materialized(  # pyright: ignore[reportPrivateUsage]
        cast(TaskRuntime, runtime),
        envelope,
        batch,
        legacy_writer_id=legacy_writer_id,
    )

    assert recovered is not None
    assert [writer for writer, _operation in lookups] == [
        harness_writer_id,
        legacy_writer_id,
    ]


def test_local_outbox_enqueue_ack_and_overflow(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-outbox")
    envelope = _envelope(session=session)
    assert store.ingest(envelope).disposition is ObservationIngestDisposition.ACCEPTED
    assert store.enqueue_outbox(workspace, "sess-outbox", envelope) is None
    assert store.pending_outbox_count(workspace) == 1
    # Duplicate identity does not grow the outbox.
    assert store.enqueue_outbox(workspace, "sess-outbox", envelope) is None
    assert store.pending_outbox_count(workspace) == 1
    assert store.acknowledge_outbox(workspace, "sess-outbox", envelope.source_identity) is True
    assert store.pending_outbox_count(workspace) == 0


def test_mapping_gap_history_does_not_latch_after_mapping_exists(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.note_coverage_gap(workspace, ObservationGapCode.MAPPING_MISSING.value)
    assert ObservationGapCode.MAPPING_MISSING.value in store.status(
        ObservationStatusQuery(workspace)
    ).gaps

    store.bind_codex_session(workspace, "mapping-healed")

    assert ObservationGapCode.MAPPING_MISSING.value not in store.status(
        ObservationStatusQuery(workspace)
    ).gaps


def test_local_outbox_v1_compatibility_and_v2_attempt_round_trip(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path, _wall=lambda: 1_767_225_600.0)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-v1-row")
    envelope = _envelope(session=session, identity="hook:v1-row")
    store.enqueue_outbox(workspace, "sess-v1-row", envelope)

    state_path = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["schema"] = "yoetz.observation-local/1"
    for row in raw["pending_outbox"]:
        row.pop("attempts")
        row.pop("last_reason")
        row.pop("last_attempt_at")
    state_path.write_text(json.dumps(raw), encoding="utf-8")

    reopened = LocalObservationStore(_state=tmp_path, _wall=lambda: 1_767_225_600.0)
    legacy = reopened.list_pending_outbox_rows(workspace)
    assert len(legacy) == 1
    assert legacy[0].attempts == 0
    assert legacy[0].last_reason is None
    assert legacy[0].last_attempt_at is None
    assert reopened.list_pending_outbox(workspace) == (("sess-v1-row", envelope),)

    attempted_at = Timestamp("2026-01-01T00:00:00.000Z")
    updated = reopened.bump_outbox_row_attempt(
        workspace,
        legacy[0],
        reason=ObservationGapCode.MAPPING_MISSING.value,
        attempted_at=attempted_at,
    )
    assert updated is not None
    durable = LocalObservationStore(_state=tmp_path).list_pending_outbox_rows(workspace)[0]
    assert durable.attempts == 1
    assert durable.last_reason == ObservationGapCode.MAPPING_MISSING.value
    assert durable.last_attempt_at == attempted_at
    assert json.loads(state_path.read_text(encoding="utf-8"))["schema"] == (
            "yoetz.observation-local/4"
    )


def test_local_store_lock_serializes_a_separate_process(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    code = (
        "from pathlib import Path; "
        "from yoetz.adapters.integrations.observation_local import LocalObservationStore; "
        f"print(LocalObservationStore(_state=Path({str(tmp_path)!r}))"
        f".pending_outbox_count({workspace!r}), flush=True)"
    )
    with store._lock:  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        process = subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.1)
        assert process.poll() is None
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert stdout.strip() == "0"


def test_first_key_creation_is_identical_across_processes(tmp_path: Path) -> None:
    code = (
        "from pathlib import Path; "
        "from yoetz.adapters.integrations.observation_local import LocalObservationStore; "
        f"print(LocalObservationStore(_state=Path({str(tmp_path)!r}))"
        f".workspace_commitment({str(tmp_path.resolve())!r}), flush=True)"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            cwd=tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=5) for process in processes]
    for process, (_, stderr) in zip(processes, outputs, strict=True):
        assert process.returncode == 0, stderr
    commitments = [stdout.strip() for stdout, _ in outputs]
    assert commitments[0] == commitments[1]
    assert len((tmp_path / "observation" / "key-material.bin").read_bytes()) == 32


def test_enqueue_refuses_projected_oversize_without_losing_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.adapters.integrations.observation_local as local_mod

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-sized")
    state_path = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    maximum = state_path.stat().st_size + 256
    monkeypatch.setattr(local_mod, "_MAX_STATE_BYTES", maximum)
    envelope = _envelope(session=session, identity="hook:sized")

    assert store.enqueue_outbox(workspace, "sess-sized", envelope) == (
        ObservationGapCode.OUTBOX_OVERFLOW.value
    )
    reopened = LocalObservationStore(_state=tmp_path)
    assert reopened.consent_for(workspace) is not None
    assert reopened.pending_outbox_count(workspace) == 0
    assert state_path.stat().st_size <= maximum
    assert (
        ObservationGapCode.OUTBOX_OVERFLOW.value
        in reopened.status(ObservationStatusQuery(workspace)).gaps
    )


def test_size_compaction_accounts_for_distinct_same_source_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.adapters.integrations.observation_local as local_mod

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-compact")
    for ordinal in (1, 2):
        store.enqueue_outbox(
            workspace,
            "sess-compact",
            _envelope(session=session, identity="hook:same", ordinal=ordinal),
        )
    monkeypatch.setattr(local_mod, "_MAX_STATE_BYTES", 2_100)

    store.note_coverage_gap(workspace, ObservationGapCode.SERVICE_UNAVAILABLE.value)

    reopened = LocalObservationStore(_state=tmp_path)
    state_path = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    accounted = (
        reopened.pending_outbox_count(workspace)
        + reopened.quarantined_count(workspace)
        + int(persisted["quarantine_evicted_count"])
    )
    assert accounted == 2
    assert state_path.stat().st_size <= 2_100


def test_monotonic_samples_fenced_across_simulated_reboot(tmp_path: Path) -> None:
    """After a reboot the monotonic clock resets, so persisted samples are fenced.

    Without fencing, ``compute_observation_lifecycle`` sees ``now - progress``
    go negative and raises, crashing status. Fencing drops the incomparable
    samples so status reports DEGRADED until fresh progress in the new epoch.
    """

    from yoetz.domain.observation import ObservationLifecycle

    # Boot 1: monotonic 1000, wall 5000 -> epoch 4000.
    boot1 = LocalObservationStore(_state=tmp_path, _monotonic=lambda: 1000.0, _wall=lambda: 5000.0)
    workspace = boot1.workspace_commitment(str(tmp_path.resolve()))
    boot1.grant_consent(workspace)
    session = boot1.session_commitment("sess-epoch")
    boot1.bind_session(workspace, session)
    boot1.ingest(_envelope(session=session))
    assert boot1.status(ObservationStatusQuery(workspace)).lifecycle is ObservationLifecycle.ACTIVE

    # Reboot: monotonic reset to 5, same wall -> epoch 4995, far from 4000.
    rebooted = LocalObservationStore(_state=tmp_path, _monotonic=lambda: 5.0, _wall=lambda: 5000.0)
    # Must not raise on the now-incomparable persisted sample.
    assert (
        rebooted.status(ObservationStatusQuery(workspace)).lifecycle
        is ObservationLifecycle.DEGRADED
    )

    # Same-boot tiny drift stays within tolerance and keeps using the samples.
    same_boot = LocalObservationStore(
        _state=tmp_path, _monotonic=lambda: 1000.5, _wall=lambda: 5000.5
    )
    assert (
        same_boot.status(ObservationStatusQuery(workspace)).lifecycle is ObservationLifecycle.ACTIVE
    )


def test_session_end_reports_stopped_and_persists(tmp_path: Path) -> None:
    from yoetz.domain.observation import ObservationLifecycle

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment("sess-end")
    store.bind_session(workspace, session)
    store.ingest(_envelope(session=session))
    assert store.status(ObservationStatusQuery(workspace)).lifecycle is not (
        ObservationLifecycle.STOPPED
    )

    store.note_session_end(workspace, session)
    assert store.status(ObservationStatusQuery(workspace)).lifecycle is ObservationLifecycle.STOPPED
    # Durable across a fresh store instance.
    reopened = LocalObservationStore(_state=tmp_path)
    assert (
        reopened.status(ObservationStatusQuery(workspace)).lifecycle is ObservationLifecycle.STOPPED
    )


def test_new_session_generation_resumes_and_stale_end_remains_fenced(tmp_path: Path) -> None:
    from yoetz.domain.observation import ObservationLifecycle

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment("sess-generation")
    store.bind_session(workspace, session)
    first = store.begin_session_generation(workspace, session)
    store.note_session_end(workspace, session, generation=first)
    assert store.status(ObservationStatusQuery(workspace)).lifecycle is ObservationLifecycle.STOPPED

    second = store.begin_session_generation(workspace, session)
    assert second == first + 1
    assert store.status(ObservationStatusQuery(workspace)).lifecycle is not (
        ObservationLifecycle.STOPPED
    )
    store.note_session_end(workspace, session, generation=first)
    assert store.status(ObservationStatusQuery(workspace)).lifecycle is not (
        ObservationLifecycle.STOPPED
    )


def test_local_outbox_quarantine_is_visible_and_durable(tmp_path: Path) -> None:
    # Wall clock pinned near the fixture receipt_time (2026-01-01) so the #211
    # quarantine age bound does not see these entries as expired detail.
    store = LocalObservationStore(_state=tmp_path, _wall=lambda: 1767312000.0)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-quar")
    envelope = _envelope(session=session, identity="hook:quar")
    store.ingest(envelope)
    assert store.enqueue_outbox(workspace, "sess-quar", envelope) is None
    assert store.pending_outbox_count(workspace) == 1

    # A permanently-invalid entry is quarantined, not acknowledged as committed.
    moved = store.quarantine_outbox(
        workspace, "sess-quar", envelope.source_identity, ObservationGapCode.CONSENT_REVOKED.value
    )
    assert moved is True
    assert store.pending_outbox_count(workspace) == 0
    assert store.quarantined_count(workspace) == 1
    quarantined = store.list_quarantine(workspace)
    assert quarantined[0][0] == "sess-quar"
    assert quarantined[0][2] == ObservationGapCode.CONSENT_REVOKED.value

    # Visible as a coverage gap and durable across a fresh store instance.
    observed = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.OUTBOX_QUARANTINED.value in observed.gaps
    reopened = LocalObservationStore(_state=tmp_path)
    assert reopened.quarantined_count(workspace) == 1
    assert (
        ObservationGapCode.OUTBOX_QUARANTINED.value
        in reopened.status(ObservationStatusQuery(workspace)).gaps
    )


def test_quarantine_eviction_retains_aggregate_loss_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.adapters.integrations.observation_local as local_mod

    monkeypatch.setattr(local_mod, "_MAX_QUARANTINE", 2)
    # Wall clock pinned near the fixture receipt_time (2026-01-01) so only the
    # count bound under test — not the #211 age bound — causes evictions.
    store = LocalObservationStore(_state=tmp_path, _wall=lambda: 1767312000.0)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-quarantine-eviction")
    for index in range(3):
        envelope = _envelope(
            session=session,
            identity=f"hook:quarantine:{index}",
            ordinal=index + 1,
        )
        store.ingest(envelope)
        store.enqueue_outbox(workspace, "sess-quarantine-eviction", envelope)
        assert store.quarantine_outbox(
            workspace,
            "sess-quarantine-eviction",
            envelope.source_identity,
            ObservationGapCode.CONSENT_REVOKED.value,
        )
    assert store.quarantined_count(workspace) == 2
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.QUARANTINE_DETAIL_EVICTED.value in status.gaps
    state_bytes = b"".join(path.read_bytes() for path in tmp_path.rglob("*.json") if path.is_file())
    assert b'"quarantine_evicted_count":1' in state_bytes
    assert b'"quarantine_evicted_commitment":"sha256:' in state_bytes
    assert b'"quarantine_evicted_first":' in state_bytes
    assert b'"quarantine_evicted_last":' in state_bytes


def test_quarantine_detail_expires_by_age_into_aggregate_evidence(tmp_path: Path) -> None:
    """#211: quarantine is bounded by age, not only by count and byte cap.

    Age is measured from the store-authored quarantined_at (never the possibly
    far older envelope receipt_time), and the destructive prune is fenced on a
    trusted clock epoch, so both stores here pin wall AND monotonic clocks to
    keep the persisted epoch comparable across the simulated 15 days.
    """

    quarantine_day = 1767312000.0  # 2026-01-02, one day after the fixture receipt_time
    store = LocalObservationStore(
        _state=tmp_path, _wall=lambda: quarantine_day, _monotonic=lambda: 100.0
    )
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-quarantine-age")
    envelope = _envelope(session=session, identity="hook:quarantine-age")
    store.ingest(envelope)
    store.enqueue_outbox(workspace, "sess-quarantine-age", envelope)
    assert store.quarantine_outbox(
        workspace,
        "sess-quarantine-age",
        envelope.source_identity,
        ObservationGapCode.CONSENT_REVOKED.value,
    )
    store.note_stream_reconcile(workspace, mono=100.0)  # persists the clock epoch
    assert store.quarantine_facts(workspace) == (1, 0, 0)

    day = 86_400.0
    aged = LocalObservationStore(
        _state=tmp_path,
        _wall=lambda: quarantine_day + 15 * day,
        _monotonic=lambda: 100.0 + 15 * day,
    )
    # Any mutation-driven save prunes expired detail; a consent re-grant is the
    # cheapest one that touches no other quarantine machinery.
    aged.grant_consent(workspace, granted_at=Timestamp("2026-01-17T00:00:00.000Z"))
    assert aged.quarantine_facts(workspace) == (0, 1, 0)
    status = aged.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.QUARANTINE_DETAIL_EVICTED.value in status.gaps

    # A wall-clock jump with an unchanged monotonic clock (snapshot restore,
    # NTP correction) must NOT destroy detail: the epoch no longer matches.
    jumped = LocalObservationStore(
        _state=tmp_path,
        _wall=lambda: quarantine_day + 4 * 365 * day,
        _monotonic=lambda: 100.0 + 15 * day,
    )
    second = _envelope(session=session, identity="hook:quarantine-age-2", ordinal=2)
    jumped.ingest(second)
    jumped.enqueue_outbox(workspace, "sess-quarantine-age", second)
    assert jumped.quarantine_outbox(
        workspace,
        "sess-quarantine-age",
        second.source_identity,
        ObservationGapCode.CONSENT_REVOKED.value,
    )
    assert jumped.quarantine_facts(workspace) == (1, 1, 0), (
        "a clock jump must pause the age bound, not trigger it"
    )


def test_reclaim_quarantine_empties_detail_and_records_the_drop(tmp_path: Path) -> None:
    """#211: a recovered install sheds the quarantine tax loudly, not silently."""

    store = LocalObservationStore(_state=tmp_path, _wall=lambda: 1767312000.0)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-reclaim")
    for index in range(3):
        envelope = _envelope(session=session, identity=f"hook:reclaim:{index}", ordinal=index + 1)
        store.ingest(envelope)
        store.enqueue_outbox(workspace, "sess-reclaim", envelope)
        assert store.quarantine_outbox(
            workspace,
            "sess-reclaim",
            envelope.source_identity,
            ObservationGapCode.CONSENT_REVOKED.value,
        )
    assert store.quarantine_facts(workspace) == (3, 0, 0)
    assert store.reclaim_quarantine(workspace) == 3
    # Operator reclaims are counted separately from involuntary evictions so a
    # deliberate cleanup never reads as data loss.
    assert store.quarantine_facts(workspace) == (0, 0, 3)
    assert store.reclaim_quarantine(workspace) == 0
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.QUARANTINE_DETAIL_EVICTED.value in status.gaps
    state_bytes = b"".join(path.read_bytes() for path in tmp_path.rglob("*.json") if path.is_file())
    assert b'"quarantine_reclaimed_count":3' in state_bytes
    assert b'"quarantine_evicted_count":0' in state_bytes
    assert b'"quarantine_evicted_commitment":"sha256:' in state_bytes


def test_local_outbox_overflow_records_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import yoetz.adapters.integrations.observation_local as local_mod

    monkeypatch.setattr(local_mod, "_MAX_OUTBOX", 2)
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sess-overflow")
    for index in range(3):
        envelope = _envelope(session=session, identity=f"hook:overflow:{index}", ordinal=index + 1)
        store.ingest(envelope)
        overflow = store.enqueue_outbox(workspace, "sess-overflow", envelope)
        if index < 2:
            assert overflow is None
        else:
            assert overflow == ObservationGapCode.OUTBOX_OVERFLOW.value
    observed = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.OUTBOX_OVERFLOW.value in observed.gaps


@pytest.mark.anyio
async def test_sqlite_ingest_after_consent_bind() -> None:
    import apsw

    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_obs", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    workspace = f"hmac-sha256:{'11' * 32}"
    session = f"hmac-sha256:{'22' * 32}"
    store.grant_consent(workspace, Timestamp("2026-01-01T00:00:00.000Z"))
    store.bind_session(workspace, session)
    result = await store.ingest(_envelope(session=session))
    assert result.disposition is ObservationIngestDisposition.ACCEPTED
    duplicate = await store.ingest(_envelope(session=session))
    assert duplicate.disposition is ObservationIngestDisposition.DUPLICATE


@pytest.mark.anyio
async def test_coordinator_rejects_without_mapping(tmp_path: Path) -> None:
    class _NoRuntime:
        async def route(self, command: object) -> object:
            raise AssertionError("route must not be called without mapping")

        async def release(self, runtime: object) -> None:
            return None

    class _Clock:
        def now_utc(self) -> Timestamp:
            return Timestamp("2026-01-01T00:00:00.000Z")

    class _Ids:
        def new(self, kind: IdKind) -> str:
            return PREFIX_BY_KIND[kind] + str(uuid.uuid4())

    local = LocalObservationStore(_state=tmp_path)
    workspace = local.workspace_commitment(str(tmp_path.resolve()))
    local.grant_consent(workspace)
    session = local.bind_codex_session(workspace, "unmapped-sess")
    coordinator = ObservationCoordinator(
        runtime=_NoRuntime(),  # type: ignore[arg-type]
        local=local,
        clock=_Clock(),  # type: ignore[arg-type]
        ids=_Ids(),  # type: ignore[arg-type]
        state_root=tmp_path,
        mapping_loader=lambda *_args, **_kwargs: None,  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    )
    result = await coordinator.ingest_request(
        ObservationIngestRequest(
            codex_session_id="unmapped-sess",
            envelope=_envelope(session=session),
        )
    )
    assert result.disposition is ObservationIngestDisposition.REJECTED
    assert result.reason == ObservationGapCode.MAPPING_MISSING.value


@pytest.mark.anyio
async def test_coordinator_rejects_disabled_before_mapping_or_runtime(tmp_path: Path) -> None:
    class _NoRuntime:
        async def route(self, command: object) -> object:
            raise AssertionError("disabled observation must not route")

        async def release(self, runtime: object) -> None:
            return None

    local = LocalObservationStore(_state=tmp_path)
    workspace = local.workspace_commitment(str(tmp_path.resolve()))
    local.grant_consent(workspace)
    session = local.bind_codex_session(workspace, "disabled-sess")

    def mapping_loader(
        codex_session_id: str, *, _state: Path | None = None
    ) -> LifecycleMapping | None:
        del codex_session_id, _state
        raise AssertionError("disabled observation must not load mapping")

    coordinator = ObservationCoordinator(
        runtime=_NoRuntime(),  # type: ignore[arg-type]
        local=local,
        clock=object(),  # type: ignore[arg-type]
        ids=object(),  # type: ignore[arg-type]
        state_root=tmp_path,
        mapping_loader=mapping_loader,
        observation_enabled=False,
    )
    result = await coordinator.ingest_request(
        ObservationIngestRequest(
            codex_session_id="disabled-sess",
            envelope=_envelope(session=session),
        )
    )
    assert result.disposition is ObservationIngestDisposition.REJECTED
    assert result.reason == "observation_disabled"


def _obs(
    *, source: ObservationSource, identity: str, corr: str | None, session: str, kind: str
) -> ObservationEnvelope:
    structural: dict[str, object] = {"tool_name": "shell"}
    if corr is not None:
        structural["tool_call_id"] = corr
        structural["correlation_id"] = corr
    return ObservationEnvelope(
        session_commitment=session,
        event_kind=kind,
        source_identity=identity,
        source=source,
        cursor=ObservationCursor(1, 0, 1, f"hmac-sha256:{'ab' * 32}", "codex-obs-hook/1.1.0"),
        receipt_time=Timestamp("2026-01-01T00:00:00.000Z"),
        structural_payload=JsonObject(structural),
        content_object_refs=(),
        gap_codes=(),
    )


def test_hook_and_stream_copies_share_one_logical_operation() -> None:
    from yoetz.application.observation_materialize import (
        canonical_logical_identity,
        observation_operation_digest,
    )

    session = f"hmac-sha256:{'66' * 32}"
    hook = _obs(
        source=ObservationSource.CODEX_HOOK,
        identity="hook:post:call-1",
        corr="call-1",
        session=session,
        kind="PostToolUse",
    )
    stream = _obs(
        source=ObservationSource.CODEX_SESSION_STREAM,
        identity="stream:item.completed:call-1",
        corr="call-1",
        session=session,
        kind="item.completed",
    )
    # Same host call id + tool family -> one logical identity across sources.
    assert canonical_logical_identity(hook) == canonical_logical_identity(stream)

    def _digest(env: ObservationEnvelope) -> str:
        return observation_operation_digest(
            task_id="task_x",
            session_id="ses_x",
            writer_id="wtr_x",
            logical_identity=canonical_logical_identity(env),
            draft_roles=("action", "result"),
        )

    assert _digest(hook) == _digest(stream)

    # A different host call id stays distinct.
    other = _obs(
        source=ObservationSource.CODEX_HOOK,
        identity="hook:post:call-2",
        corr="call-2",
        session=session,
        kind="PostToolUse",
    )
    assert canonical_logical_identity(other) != canonical_logical_identity(hook)

    # No host call id -> source-specific opaque identity (hook != stream).
    opaque_hook = _obs(
        source=ObservationSource.CODEX_HOOK,
        identity="hook:session-start",
        corr=None,
        session=session,
        kind="SessionStart",
    )
    opaque_stream = _obs(
        source=ObservationSource.CODEX_SESSION_STREAM,
        identity="stream:session-start",
        corr=None,
        session=session,
        kind="SessionStart",
    )
    assert canonical_logical_identity(opaque_hook) != canonical_logical_identity(opaque_stream)


@pytest.mark.anyio
async def test_run_advice_persists_snapshot_with_real_datetime_clock(tmp_path: Path) -> None:
    """Regression: production clocks return ``datetime``, not ``Timestamp``.

    ``_run_advice`` must persist the advice snapshot through the durable
    observation store, which requires a ``Timestamp``. A raw ``datetime`` would
    ``AttributeError`` on ``updated_at.wire`` and get swallowed by the ingest
    guard, silently dropping advice. This exercises the real datetime path.
    """

    from datetime import UTC, datetime

    import apsw

    from yoetz.adapters.sqlite.migrations import initialize_bundle

    class _DatetimeClock:
        def now_utc(self) -> datetime:
            # Mirror the production ``_SystemClock``: a real ``datetime`` truncated
            # to millisecond precision (never a ``Timestamp``).
            now = datetime.now(UTC)
            return now.replace(microsecond=(now.microsecond // 1_000) * 1_000)

    class _UnusedRuntime:
        async def route(self, command: object) -> object:
            raise AssertionError("route must not be called in this unit path")

        async def release(self, runtime: object) -> None:
            return None

    class _Ids:
        def new(self, kind: IdKind) -> str:
            return PREFIX_BY_KIND[kind] + str(uuid.uuid4())

    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_advice", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    workspace = f"hmac-sha256:{'44' * 32}"
    session = f"hmac-sha256:{'55' * 32}"
    store.grant_consent(workspace, Timestamp("2026-01-01T00:00:00.000Z"))
    store.bind_session(workspace, session)
    ingested = await store.ingest(
        _envelope(session=session, kind="PostToolUse", identity="hook:fail", exit_status=2)
    )
    assert ingested.disposition is ObservationIngestDisposition.ACCEPTED
    assert store.load_advice_snapshot(workspace) is None

    local = LocalObservationStore(_state=tmp_path)
    coordinator = ObservationCoordinator(
        runtime=_UnusedRuntime(),  # type: ignore[arg-type]
        local=local,
        clock=_DatetimeClock(),  # type: ignore[arg-type]
        ids=_Ids(),  # type: ignore[arg-type]
        state_root=tmp_path,
    )

    await coordinator._run_advice(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        workspace, "task_advice", store
    )

    persisted = store.load_advice_snapshot(workspace)
    assert persisted is not None
    assert persisted.ranked_finding_ids


@pytest.mark.anyio
async def test_advice_finding_materialization_uses_canonical_schema_and_envelope_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Advice detail may retain mixed coverage; its append envelope may not.

    The ledger validates the publication-channel coverage on every append entry.
    This regression also locks the canonical underscore spelling of the event
    schema so a post-ingest advice failure cannot strand an otherwise durable
    observation in the local outbox.
    """

    from datetime import UTC, datetime
    from types import SimpleNamespace

    from yoetz.application import observation_coordinator as coordinator_module
    from yoetz.application.unit_of_work import PreparedMutation
    from yoetz.domain.observation import AdviceItem, AdviceSnapshot
    from yoetz.ports.ledger import ProjectionView
    from yoetz.ports.objects import ObjectRef
    from yoetz.protocol.coverage import PublicationChannel, coverage_for_channel, weakest

    task_id = PREFIX_BY_KIND[IdKind.TASK] + str(uuid.uuid4())
    session_id = PREFIX_BY_KIND[IdKind.SESSION] + str(uuid.uuid4())
    writer_id = PREFIX_BY_KIND[IdKind.WRITER] + str(uuid.uuid4())
    item_coverage = weakest(
        coverage_for_channel(PublicationChannel.HOOK_OBSERVED),
        coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
    )
    item = AdviceItem(
        finding_id=finding_id(PREFIX_BY_KIND[IdKind.FINDING] + str(uuid.uuid4())),
        rule_code="failed_command_unresolved",
        priority=10,
        summary="A failed command remains unresolved.",
        detail="Resolve the failed command and rerun the check.",
        recommended_next_action="rerun_check",
        evidence_refs=("hook:advice-materialization",),
        coverage=item_coverage,
        freshness_frontier="frontier-1",
    )
    snapshot = AdviceSnapshot(
        ranked_finding_ids=(item.finding_id,),
        evidence_basis_digest="sha256:" + "a" * 64,
        confidence_coverage=item_coverage,
        recommended_next_action="rerun_check",
        freshness_frontier="frontier-1",
        suppression_identity="advice-materialization-1",
        ranked_items=(item,),
    )

    class _Ledger:
        async def load_projection(self, loaded_session_id: str, view: ProjectionView):
            assert loaded_session_id == session_id
            assert view in {ProjectionView.COMPACT, ProjectionView.CANDIDATE_FINDINGS}
            return None

        async def _events(self):
            if False:
                yield None

        def load_events(self, loaded_session_id: str):
            assert loaded_session_id == session_id
            return self._events()

        async def lookup_operation(self, loaded_writer_id: str, operation_id: str):
            assert loaded_writer_id == writer_id
            assert operation_id.startswith(PREFIX_BY_KIND[IdKind.REQUEST])
            return None

    class _Objects:
        async def stage(self, source: object, metadata: object):
            return source, metadata

        async def finalize(self, staged: tuple[object, object]) -> ObjectRef:
            source, metadata = staged
            data = source.data  # type: ignore[attr-defined]
            assert isinstance(data, bytes)
            return ObjectRef(
                PREFIX_BY_KIND[IdKind.OBJECT] + str(uuid.uuid4()),
                len(data),
                "hmac-sha256:" + "b" * 64,
                "sha256:" + "c" * 64,
                "yoetz-object/1",
                "test-key-1",
                metadata,  # type: ignore[arg-type]
            )

    captured: list[PreparedMutation] = []

    async def _capture_append(ledger: object, prepared: PreparedMutation):
        assert ledger is runtime.ledger
        captured.append(prepared)
        return None

    monkeypatch.setattr(coordinator_module, "run_prepared_append", _capture_append)

    class _Clock:
        def now_utc(self) -> datetime:
            return datetime(2026, 1, 1, tzinfo=UTC)

    class _Ids:
        def new(self, kind: IdKind) -> str:
            return PREFIX_BY_KIND[kind] + str(uuid.uuid4())

    class _UnusedRuntimePort:
        async def route(self, command: object) -> object:
            raise AssertionError("route must not be called")

        async def release(self, runtime: object) -> None:
            return None

    runtime = SimpleNamespace(
        task_id=task_id,
        session_id=session_id,
        writer_id=writer_id,
        ledger=_Ledger(),
        objects=_Objects(),
    )
    coordinator = ObservationCoordinator(
        runtime=_UnusedRuntimePort(),  # type: ignore[arg-type]
        local=LocalObservationStore(_state=tmp_path),
        clock=_Clock(),  # type: ignore[arg-type]
        ids=_Ids(),  # type: ignore[arg-type]
        state_root=tmp_path,
    )
    envelope = _envelope(
        session=f"hmac-sha256:{'d' * 64}",
        kind="PostToolUse",
        identity="hook:advice-materialization",
        exit_status=2,
    )

    await coordinator._materialize_advice_findings(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        cast(TaskRuntime, runtime),
        (envelope,),
        snapshot,  # type: ignore[arg-type]
    )

    assert len(captured) == 1
    entries = captured[0].command.entries
    assert len(entries) == 1
    assert entries[0].draft.schema.name == "finding_recorded"
    assert entries[0].draft.payload.coverage == item_coverage  # type: ignore[union-attr]
    assert entries[0].publication_channel is PublicationChannel.ENGINE_DERIVED
    assert entries[0].coverage == coverage_for_channel(PublicationChannel.ENGINE_DERIVED)


@pytest.mark.anyio
async def test_advice_materialization_failure_does_not_advance_suppression_snapshot(
    tmp_path: Path,
) -> None:
    """A failed finding append must remain retryable with the same snapshot."""

    from datetime import UTC, datetime

    from yoetz.domain.observation import AdviceItem, AdviceSnapshot
    from yoetz.ports.runtime import OwnershipFence, RuntimeCapability, TaskRuntime
    from yoetz.protocol.coverage import PublicationChannel, coverage_for_channel

    task_id = PREFIX_BY_KIND[IdKind.TASK] + str(uuid.uuid4())
    session_id = PREFIX_BY_KIND[IdKind.SESSION] + str(uuid.uuid4())
    writer_id = PREFIX_BY_KIND[IdKind.WRITER] + str(uuid.uuid4())
    workspace = f"hmac-sha256:{'e' * 64}"
    coverage = coverage_for_channel(PublicationChannel.ENGINE_DERIVED)
    item = AdviceItem(
        finding_id=finding_id(PREFIX_BY_KIND[IdKind.FINDING] + str(uuid.uuid4())),
        rule_code="failed_command_unresolved",
        priority=1,
        summary="A failed command remains unresolved.",
        detail="Resolve the failed command and rerun the check.",
        recommended_next_action="rerun_check",
        evidence_refs=(),
        coverage=coverage,
        freshness_frontier="frontier-retry",
    )
    snapshot = AdviceSnapshot(
        ranked_finding_ids=(item.finding_id,),
        evidence_basis_digest="sha256:" + "f" * 64,
        confidence_coverage=coverage,
        recommended_next_action="rerun_check",
        freshness_frontier="frontier-retry",
        suppression_identity="advice-retry-1",
        ranked_items=(item,),
    )
    envelope = _envelope(
        session=f"hmac-sha256:{'1' * 64}",
        kind="PostToolUse",
        identity="hook:advice-retry",
        exit_status=2,
    )

    class _Store:
        published: list[AdviceSnapshot] = []

        def list_envelopes(self, loaded_workspace: str):
            assert loaded_workspace == workspace
            return (envelope,)

        def set_advice_snapshot(
            self, loaded_workspace: str, published: AdviceSnapshot, updated_at: Timestamp
        ) -> None:
            assert loaded_workspace == workspace
            assert type(updated_at) is Timestamp
            self.published.append(published)

        def set_session_advice_snapshot(self, **kwargs: object) -> None:
            assert kwargs["workspace"] == workspace

        def record_advice_history(self, **kwargs: object) -> None:
            assert kwargs["workspace"] == workspace

    class _Builder:
        async def build(self, *args: object, **kwargs: object) -> AdviceSnapshot:
            return snapshot

    class _Clock:
        def now_utc(self) -> datetime:
            return datetime(2026, 1, 1, tzinfo=UTC)

    class _Ids:
        def new(self, kind: IdKind) -> str:
            return PREFIX_BY_KIND[kind] + str(uuid.uuid4())

    attempts = 0

    class _RetryCoordinator(ObservationCoordinator):
        async def _materialize_advice_findings(  # type: ignore[override]
            self,
            runtime: TaskRuntime,
            envelopes: tuple[ObservationEnvelope, ...],
            value: object,
            **_kwargs: object,
        ) -> None:
            nonlocal attempts
            attempts += 1
            assert envelopes == (envelope,)
            assert value is snapshot
            if attempts == 1:
                raise RuntimeError("synthetic_append_failure")

    runtime = TaskRuntime(
        task_id,
        session_id,
        writer_id,
        frozenset({RuntimeCapability.WRITE}),
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        "0.1.0",
        "0.1.0",
        "0.1",
        "1.0.0",
        OwnershipFence(
            PREFIX_BY_KIND[IdKind.SERVICE_INSTANCE] + str(uuid.uuid4()),
            1,
            1,
            "retry_test_nonce",
        ),
    )
    local = LocalObservationStore(_state=tmp_path)
    store = _Store()
    coordinator = _RetryCoordinator(
        runtime=object(),  # type: ignore[arg-type]
        local=local,
        clock=_Clock(),  # type: ignore[arg-type]
        ids=_Ids(),  # type: ignore[arg-type]
        state_root=tmp_path,
        advice_context_builder=_Builder(),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="synthetic_append_failure"):
        await coordinator._run_advice(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            workspace,
            runtime,
            store,  # type: ignore[arg-type]
        )
    assert store.published == []
    assert local.advice_snapshot_for(workspace) is None

    await coordinator._run_advice(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        workspace,
        runtime,
        store,  # type: ignore[arg-type]
    )
    assert attempts == 2
    assert store.published == [snapshot]
    assert local.advice_snapshot_for(workspace) == snapshot


@pytest.mark.anyio
async def test_duplicate_ingest_reconciles_ledger_instead_of_early_return(tmp_path: Path) -> None:
    """Regression: a DUPLICATE observation row must still reconcile the ledger.

    The observation row can already exist while an earlier ledger append failed
    (and its retryable rejection kept the outbox entry pending). On retry the
    store reports DUPLICATE; the coordinator must NOT return early — it must
    re-run the idempotent materialize/append to repair the missing ledger
    operation and refresh advice.
    """

    from yoetz.adapters.integrations.codex_lifecycle import LifecycleMapping

    calls = {"append": 0, "advice": 0}

    class _DuplicateStore:
        def grant_consent(self, *args: object) -> None:
            return None

        def bind_session(self, *args: object) -> None:
            return None

        async def ingest(self, envelope: ObservationEnvelope):
            return ObservationIngestResult(
                ObservationIngestDisposition.DUPLICATE, None, envelope.cursor
            )

    class _Runtime:
        task_id = PREFIX_BY_KIND[IdKind.TASK] + str(uuid.uuid4())
        session_id = PREFIX_BY_KIND[IdKind.SESSION] + str(uuid.uuid4())
        writer_id = PREFIX_BY_KIND[IdKind.WRITER] + str(uuid.uuid4())
        observation = _DuplicateStore()

    class _RuntimePort:
        async def route(self, command: object) -> object:
            return _Runtime()

        async def release(self, runtime: object) -> None:
            return None

    class _Clock:
        def now_utc(self) -> Timestamp:
            return Timestamp("2026-01-01T00:00:00.000Z")

    class _Ids:
        def new(self, kind: IdKind) -> str:
            return PREFIX_BY_KIND[kind] + str(uuid.uuid4())

    class _RecordingCoordinator(ObservationCoordinator):
        async def _append_materialized(  # type: ignore[override]  # noqa: SLF001
            self, runtime: object, envelope: object, batch: object, **_kwargs: object
        ):
            calls["append"] += 1

        async def _run_advice(  # type: ignore[override]  # noqa: SLF001
            self, workspace: str, task_id: str, store: object, **_kwargs: object
        ):
            calls["advice"] += 1

    local = LocalObservationStore(_state=tmp_path)
    workspace = local.workspace_commitment(str(tmp_path.resolve()))
    local.grant_consent(workspace)
    codex_id = "codex-dup-repair"
    session = local.bind_codex_session(workspace, codex_id)

    mapping = LifecycleMapping(
        mapping_version=1,
        codex_session_id=codex_id,
        yoetz_task_id=_Runtime.task_id,
        yoetz_session_id=_Runtime.session_id,
        yoetz_writer_id=_Runtime.writer_id,
        last_frontier=None,
    )

    coordinator = _RecordingCoordinator(
        runtime=_RuntimePort(),  # type: ignore[arg-type]
        local=local,
        clock=_Clock(),  # type: ignore[arg-type]
        ids=_Ids(),  # type: ignore[arg-type]
        state_root=tmp_path,
        mapping_loader=lambda *_args, **_kwargs: mapping,  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    )

    result = await coordinator.ingest_request(
        ObservationIngestRequest(
            codex_session_id=codex_id,
            envelope=_envelope(
                session=session, kind="PostToolUse", identity="hook:dup", exit_status=1
            ),
        )
    )

    # Reported disposition stays DUPLICATE, but reconciliation still ran.
    assert result.disposition is ObservationIngestDisposition.DUPLICATE
    assert calls["append"] == 1, "duplicate must still reconcile the ledger append"
    assert calls["advice"] == 1, "duplicate must still refresh advice"


@pytest.mark.anyio
async def test_control_handlers_accept_ingest_request(tmp_path: Path) -> None:
    class _Port:
        async def ingest_request(self, request: ObservationIngestRequest):
            assert request.codex_session_id == "sess-control"
            from yoetz.domain.observation import ObservationIngestResult

            return ObservationIngestResult(
                ObservationIngestDisposition.ACCEPTED,
                None,
                request.envelope.cursor,
            )

        async def status(self, query: object) -> object:
            raise AssertionError("unused")

        async def pause(self, command: object) -> object:
            raise AssertionError("unused")

        async def resume(self, command: object) -> object:
            raise AssertionError("unused")

        async def revoke(self, command: object) -> object:
            raise AssertionError("unused")

    handlers = build_observation_support_handlers(_Port())  # type: ignore[arg-type]
    from yoetz.ports.control import ControlMethod

    session = f"hmac-sha256:{'33' * 32}"
    body = observation_ingest_request_to_json(
        ObservationIngestRequest(
            codex_session_id="sess-control",
            envelope=_envelope(session=session),
        )
    )
    result = await handlers[ControlMethod.OBSERVATION_INGEST](body)
    assert result["disposition"] == "accepted"
