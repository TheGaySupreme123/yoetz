"""Focused routing and service-side observation outbox sweep tests."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from yoetz.adapters.integrations.observation_local import (
    LocalObservationStore,
    ObservationOutboxRow,
)
from yoetz.application.observation_drain import (
    ObservationDrainAction,
    ObservationOutboxSweeper,
    route_observation_ingest,
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
)
from yoetz.domain.values import JsonObject, Timestamp


def _envelope(session: str, identity: str, ordinal: int) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=session,
        event_kind="PostToolUse",
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            1,
            0,
            ordinal,
            f"hmac-sha256:{'ab' * 32}",
            "codex-obs-hook/1.0.0",
        ),
        receipt_time=Timestamp("2026-01-01T00:00:00.000Z"),
        structural_payload=JsonObject(
            {"tool_name": "shell", "tool_call_id": identity, "exit_status": 0}
        ),
        content_object_refs=(),
        gap_codes=(),
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, "duplicate", None),
            ObservationDrainAction.ACKNOWLEDGE,
        ),
        (
            ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.MAPPING_MISSING.value,
                None,
            ),
            ObservationDrainAction.RETRY,
        ),
        (
            ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                "observation_disabled",
                None,
            ),
            ObservationDrainAction.RETRY,
        ),
        (
            ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.CONSENT_REVOKED.value,
                None,
            ),
            ObservationDrainAction.QUARANTINE,
        ),
        (
            ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value,
                None,
            ),
            ObservationDrainAction.QUARANTINE,
        ),
    ],
)
def test_route_observation_ingest_is_pure(
    result: ObservationIngestResult, expected: ObservationDrainAction
) -> None:
    assert route_observation_ingest(result).action is expected


def test_route_unknown_rejection_reason_to_safe_retry_fallback() -> None:
    decision = route_observation_ingest(
        ObservationIngestResult(
            ObservationIngestDisposition.REJECTED,
            "customerapikey123",
            None,
        )
    )

    assert decision.action is ObservationDrainAction.RETRY
    assert decision.reason == ObservationGapCode.SERVICE_UNAVAILABLE.value


def test_bulk_quarantine_records_terminal_reason_and_returns_rows_moved(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    corrupt = store.bind_codex_session(workspace, "corrupt")
    healthy = store.bind_codex_session(workspace, "healthy")
    for ordinal in (1, 2):
        store.enqueue_outbox(
            workspace,
            "corrupt",
            _envelope(corrupt, f"hook:corrupt:{ordinal}", ordinal),
        )
    store.enqueue_outbox(workspace, "healthy", _envelope(healthy, "hook:healthy", 1))

    moved = store.quarantine_outbox_session(
        workspace,
        "corrupt",
        ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value,
    )

    assert moved == 2
    assert (
        store.quarantine_outbox_session(
            workspace,
            "corrupt",
            ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value,
        )
        == 0
    )
    assert [row.codex_session_id for row in store.list_pending_outbox_rows(workspace)] == [
        "healthy"
    ]
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value in status.gaps
    assert ObservationGapCode.OUTBOX_QUARANTINED.value in status.gaps


def test_successful_recovery_probe_heals_only_its_corrupt_session(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    first = store.bind_codex_session(workspace, "corrupt-first")
    second = store.bind_codex_session(workspace, "corrupt-second")
    store.enqueue_outbox(workspace, "corrupt-first", _envelope(first, "hook:first:1", 1))
    store.enqueue_outbox(workspace, "corrupt-second", _envelope(second, "hook:second:1", 1))
    reason = ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value
    assert store.quarantine_outbox_session(workspace, "corrupt-first", reason) == 1
    assert store.quarantine_outbox_session(workspace, "corrupt-second", reason) == 1

    assert store.ingest(_envelope(first, "hook:first:repaired", 2)).disposition is (
        ObservationIngestDisposition.ACCEPTED
    )
    assert reason in store.status(ObservationStatusQuery(workspace)).gaps

    assert store.ingest(_envelope(second, "hook:second:repaired", 2)).disposition is (
        ObservationIngestDisposition.ACCEPTED
    )
    healed = store.status(ObservationStatusQuery(workspace))
    assert reason not in healed.gaps
    assert ObservationGapCode.OUTBOX_QUARANTINED.value in healed.gaps


class _Coordinator:
    def __init__(self, outcomes: dict[str, ObservationIngestResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    async def ingest_request(self, request: ObservationIngestRequest) -> ObservationIngestResult:
        identity = request.envelope.source_identity
        self.calls.append(request.codex_session_id)
        outcome = self.outcomes[identity]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.anyio
async def test_sweep_routes_rows_persists_attempts_and_marks_success(tmp_path: Path) -> None:
    store = LocalObservationStore(
        _state=tmp_path,
        _monotonic=lambda: 50.0,
        _wall=lambda: 1_767_225_600.0,
    )
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "sweep-session")
    accepted = _envelope(session, "hook:accepted", 1)
    retry = _envelope(session, "hook:retry", 2)
    permanent = _envelope(session, "hook:permanent", 3)
    failed = _envelope(session, "hook:failed", 4)
    for envelope in (accepted, retry, permanent, failed):
        store.enqueue_outbox(workspace, "sweep-session", envelope)

    coordinator = _Coordinator(
        {
            accepted.source_identity: ObservationIngestResult(
                ObservationIngestDisposition.DUPLICATE, "duplicate", None
            ),
            retry.source_identity: ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.MAPPING_MISSING.value,
                None,
            ),
            permanent.source_identity: ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.CONSENT_REVOKED.value,
                None,
            ),
            failed.source_identity: RuntimeError("transport detail must not persist"),
        }
    )
    summary = await ObservationOutboxSweeper(store, coordinator).sweep()

    assert summary.attempted == 4
    assert summary.acknowledged == 1
    assert summary.retry_pending == 2
    assert summary.quarantined == 1
    assert summary.reasons == (
        (ObservationGapCode.CONSENT_REVOKED.value, 1),
        (ObservationGapCode.MAPPING_MISSING.value, 1),
        (ObservationGapCode.SERVICE_UNAVAILABLE.value, 1),
    )
    assert store.last_successful_drain_mono(workspace) == 50.0
    assert store.quarantined_count(workspace) == 1
    pending = store.list_pending_outbox_rows(workspace)
    assert [row.envelope.source_identity for row in pending] == [
        retry.source_identity,
        failed.source_identity,
    ]
    assert [row.attempts for row in pending] == [1, 1]
    assert [row.last_reason for row in pending] == [
        ObservationGapCode.MAPPING_MISSING.value,
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
    ]
    state_bytes = b"".join(path.read_bytes() for path in tmp_path.rglob("*.json"))
    assert b"transport detail must not persist" not in state_bytes


@pytest.mark.anyio
async def test_sweep_round_robins_pending_workspaces_under_limit(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    outcomes: dict[str, ObservationIngestResult | Exception] = {}
    expected_sessions: set[str] = set()
    for workspace_name in ("one", "two"):
        workspace = store.workspace_commitment(str((tmp_path / workspace_name).resolve()))
        store.grant_consent(workspace)
        codex_session = f"session-{workspace_name}"
        expected_sessions.add(codex_session)
        session = store.bind_codex_session(workspace, codex_session)
        for ordinal in (1, 2):
            envelope = _envelope(session, f"hook:{workspace_name}:{ordinal}", ordinal)
            store.enqueue_outbox(workspace, codex_session, envelope)
            outcomes[envelope.source_identity] = ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.MAPPING_MISSING.value,
                None,
            )

    coordinator = _Coordinator(outcomes)
    summary = await ObservationOutboxSweeper(store, coordinator, limit=2).sweep()

    assert summary.attempted == 2
    assert set(coordinator.calls) == expected_sessions
    assert len(store.pending_workspaces()) == 2


@pytest.mark.anyio
async def test_storage_corrupt_quarantines_session_backlog_and_keeps_healthy_lane(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    corrupt_commitment = store.bind_codex_session(workspace, "corrupt-session")
    healthy_commitment = store.bind_codex_session(workspace, "healthy-session")
    outcomes: dict[str, ObservationIngestResult | Exception] = {}
    for ordinal in (1, 2, 3):
        envelope = _envelope(corrupt_commitment, f"hook:corrupt:{ordinal}", ordinal)
        store.enqueue_outbox(workspace, "corrupt-session", envelope)
        outcomes[envelope.source_identity] = ObservationIngestResult(
            ObservationIngestDisposition.REJECTED,
            ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value,
            None,
        )
    healthy = _envelope(healthy_commitment, "hook:healthy", 1)
    store.enqueue_outbox(workspace, "healthy-session", healthy)
    outcomes[healthy.source_identity] = ObservationIngestResult(
        ObservationIngestDisposition.ACCEPTED,
        None,
        healthy.cursor,
    )

    coordinator = _Coordinator(outcomes)
    summary = await ObservationOutboxSweeper(store, coordinator).sweep()

    assert coordinator.calls.count("corrupt-session") == 1
    assert coordinator.calls.count("healthy-session") == 1
    assert summary.attempted == 2
    assert summary.acknowledged == 1
    assert summary.quarantined == 3
    assert summary.reasons == ((ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value, 1),)
    assert store.pending_outbox_count(workspace) == 0
    assert store.quarantined_count(workspace) == 3
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value in status.gaps
    assert ObservationGapCode.OUTBOX_QUARANTINED.value in status.gaps
    assert store.reclaim_quarantine(workspace) == 3


@pytest.mark.anyio
async def test_repeated_limit_one_sweeps_are_fair_across_sessions(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    outcomes: dict[str, ObservationIngestResult | Exception] = {}
    for session_name in ("a", "b"):
        codex_session = f"session-{session_name}"
        session = store.bind_codex_session(workspace, codex_session)
        envelope = _envelope(session, f"hook:{session_name}", 1)
        store.enqueue_outbox(workspace, codex_session, envelope)
        outcomes[envelope.source_identity] = ObservationIngestResult(
            ObservationIngestDisposition.REJECTED,
            ObservationGapCode.MAPPING_MISSING.value,
            None,
        )
    coordinator = _Coordinator(outcomes)
    sweeper = ObservationOutboxSweeper(store, coordinator, limit=1)

    await sweeper.sweep()
    await sweeper.sweep()

    assert coordinator.calls == ["session-a", "session-b"]


@pytest.mark.anyio
async def test_repeated_sweeps_advance_beyond_retryable_limit_prefix(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "long-session")
    outcomes: dict[str, ObservationIngestResult | Exception] = {}
    final_identity = "hook:long:65"
    for ordinal in range(1, 66):
        identity = f"hook:long:{ordinal}"
        envelope = _envelope(session, identity, ordinal)
        store.enqueue_outbox(workspace, "long-session", envelope)
        outcomes[identity] = ObservationIngestResult(
            ObservationIngestDisposition.REJECTED,
            ObservationGapCode.MAPPING_MISSING.value,
            None,
        )
    coordinator = _Coordinator(outcomes)
    sweeper = ObservationOutboxSweeper(store, coordinator, limit=64)

    await sweeper.sweep()
    assert final_identity not in {
        row.envelope.source_identity
        for row in store.list_pending_outbox_rows(workspace)
        if row.attempts > 0
    }
    await sweeper.sweep()
    assert final_identity in {
        row.envelope.source_identity
        for row in store.list_pending_outbox_rows(workspace)
        if row.attempts > 0
    }


@pytest.mark.anyio
async def test_overlapping_sweeps_cannot_resolve_uningested_same_source_successor(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "overlap-session")
    first = _envelope(session, "hook:same-source", 1)
    second = _envelope(session, "hook:same-source", 2)
    store.enqueue_outbox(workspace, "overlap-session", first)
    store.enqueue_outbox(workspace, "overlap-session", second)

    accepted = ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, "duplicate", None)

    class _OverlappingCoordinator:
        def __init__(self) -> None:
            self.calls: list[int] = []
            self.nested = False

        async def ingest_request(
            self, request: ObservationIngestRequest
        ) -> ObservationIngestResult:
            self.calls.append(request.envelope.cursor.event_position)
            if not self.nested:
                self.nested = True
                await ObservationOutboxSweeper(store, self, limit=1).sweep()
            return accepted

    coordinator = _OverlappingCoordinator()
    await ObservationOutboxSweeper(store, coordinator, limit=1).sweep()

    assert coordinator.calls == [1]
    remaining = store.list_pending_outbox_rows(workspace)
    assert len(remaining) == 1
    assert remaining[0].envelope.cursor.event_position == 2


class _BlockingStore(LocalObservationStore):
    """A store whose calls block their thread, standing in for flock plus a full re-encode."""

    delay = 0.05

    def pending_workspaces(self) -> tuple[str, ...]:
        time.sleep(self.delay)
        return super().pending_workspaces()

    def list_pending_outbox_rows(
        self, workspace: str, *, codex_session_id: str | None = None
    ) -> tuple[ObservationOutboxRow, ...]:
        time.sleep(self.delay)
        return super().list_pending_outbox_rows(workspace, codex_session_id=codex_session_id)

    def bump_outbox_row_attempt(
        self,
        workspace: str,
        expected: ObservationOutboxRow,
        *,
        reason: str | None,
        attempted_at: Timestamp | None = None,
    ) -> ObservationOutboxRow | None:
        time.sleep(self.delay)
        return super().bump_outbox_row_attempt(
            workspace, expected, reason=reason, attempted_at=attempted_at
        )

    def acknowledge_outbox_row(self, workspace: str, expected: ObservationOutboxRow) -> bool:
        time.sleep(self.delay)
        return super().acknowledge_outbox_row(workspace, expected)


def _accepted_backlog(
    store: LocalObservationStore, tmp_path: Path, *, rows: int
) -> tuple[str, dict[str, ObservationIngestResult | Exception]]:
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.bind_codex_session(workspace, "loop-session")
    outcomes: dict[str, ObservationIngestResult | Exception] = {}
    for ordinal in range(1, rows + 1):
        envelope = _envelope(session, f"hook:loop:{ordinal}", ordinal)
        store.enqueue_outbox(workspace, "loop-session", envelope)
        outcomes[envelope.source_identity] = ObservationIngestResult(
            ObservationIngestDisposition.DUPLICATE, "duplicate", None
        )
    return workspace, outcomes


@pytest.mark.anyio
async def test_sweep_never_blocks_the_event_loop(tmp_path: Path) -> None:
    """Control work keeps its share of the loop while a sweep does its blocking store I/O."""

    store = _BlockingStore(_state=tmp_path)
    _workspace, outcomes = _accepted_backlog(store, tmp_path, rows=3)
    beats: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            beats.append(loop.time())
            await asyncio.sleep(0.01)

    pulse = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.02)
    await ObservationOutboxSweeper(store, _Coordinator(outcomes)).sweep()
    stop.set()
    await pulse

    gaps = [later - earlier for earlier, later in zip(beats, beats[1:], strict=False)]
    assert len(gaps) > 4
    assert max(gaps) < 0.04


@pytest.mark.anyio
async def test_sweep_deadline_is_now_enforceable(tmp_path: Path) -> None:
    """``wait_for`` cannot preempt synchronous work; the sweep must give it await points."""

    store = _BlockingStore(_state=tmp_path)
    store.delay = 0.3
    _workspace, outcomes = _accepted_backlog(store, tmp_path, rows=2)
    sweeper = ObservationOutboxSweeper(store, _Coordinator(outcomes))
    started = asyncio.get_running_loop().time()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(sweeper.sweep(), timeout=0.2)

    assert asyncio.get_running_loop().time() - started < 0.4


class _SweepAbort(BaseException):
    """Not an Exception, so the sweeper's per-row guard cannot swallow it."""


@pytest.mark.anyio
async def test_drain_lease_is_released_even_when_a_row_raises(tmp_path: Path) -> None:
    """Entering and leaving the lease in different worker threads must still be balanced."""

    store = LocalObservationStore(_state=tmp_path)
    _workspace, _outcomes = _accepted_backlog(store, tmp_path, rows=1)
    exits = 0
    original = store.drain_lease

    @contextlib.contextmanager
    def counting(workspace: str) -> Generator[bool]:
        nonlocal exits
        with original(workspace) as owned:
            try:
                yield owned
            finally:
                exits += 1

    store.drain_lease = counting  # pyright: ignore[reportAttributeAccessIssue]

    class _Aborting:
        async def ingest_request(
            self, request: ObservationIngestRequest
        ) -> ObservationIngestResult:
            del request
            raise _SweepAbort("row abort")

    with pytest.raises(_SweepAbort):
        await ObservationOutboxSweeper(store, _Aborting()).sweep()

    assert exits == 1
