"""Focused tests for observation coordinator, materialization, and local outbox."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.application.observation_control import build_observation_support_handlers
from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.application.observation_materialize import materialize_observation_envelope
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
from yoetz.domain.values import JsonObject, Timestamp
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


def test_local_outbox_quarantine_is_visible_and_durable(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
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
        mapping_loader=lambda *_args, **_kwargs: None,
    )
    result = await coordinator.ingest_request(
        ObservationIngestRequest(
            codex_session_id="unmapped-sess",
            envelope=_envelope(session=session),
        )
    )
    assert result.disposition is ObservationIngestDisposition.REJECTED
    assert result.reason == ObservationGapCode.MAPPING_MISSING.value


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
        async def _append_materialized(self, runtime: object, envelope: object, batch: object):  # type: ignore[override]  # noqa: SLF001
            calls["append"] += 1

        async def _run_advice(self, workspace: str, task_id: str, store: object):  # type: ignore[override]  # noqa: SLF001
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
        mapping_loader=lambda *_args, **_kwargs: mapping,
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
