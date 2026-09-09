"""Bounded asynchronous observation-advice semantic review (issue #619).

Every test drives the real SQLite repository over an in-memory bundle and the real advice
builder. No provider is ever called: the dispatch is a fake that records what it was handed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

import apsw
import pytest

from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation_advice_semantic import (
    SqliteObservationAdviceSemanticRepository,
)
from yoetz.application.observation_advice import (
    ADVICE_SEMANTIC_PENDING_GAP,
    ADVICE_SEMANTIC_UNAVAILABLE_GAP,
    ObservationAdviceContextBuilder,
)
from yoetz.application.observation_advice_semantic import (
    ADVICE_SEMANTIC_FAILURE_REASONS,
    AdviceSemanticDrainHandle,
    ObservationAdviceSemanticAttempt,
    ObservationAdviceSemanticOutcome,
    ObservationAdviceSemanticScheduler,
    ObservationAdviceSemanticSupervisor,
    ObservationAdviceSemanticWorker,
    addon_from_attempt,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationLifecycle,
    ObservationSource,
    ObservationStatus,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.protocol.canonical import strict_json_parse
from yoetz.protocol.coverage import CheckType

_COMMITMENT = "hmac-sha256:" + "a" * 64
_TIME = Timestamp("2026-09-08T21:00:00.000Z")
_SESSION = "ses_00000000-0000-4000-8000-000000000001"
_GAPS = (ObservationGapCode.SOURCE_LAG.value, ObservationGapCode.VERIFICATION_STALE.value)


def _envelope(identity: str, payload: dict[str, object], *, pos: int = 1) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=_COMMITMENT,
        event_kind="PostToolUse",
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=pos * 8,
            event_position=pos,
            last_source_commitment=_COMMITMENT,
            mapping_version="codex-obs-hook/1.0.0",
        ),
        receipt_time=_TIME,
        structural_payload=JsonObject(payload),
        content_object_refs=(),
        gap_codes=(),
    )


def _repository() -> tuple[apsw.Connection, SqliteObservationAdviceSemanticRepository]:
    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "tsk_advice", "owner_generation": "1"})
    return db, SqliteObservationAdviceSemanticRepository(db)


@dataclass
class _Store:
    """The slice of the observation store the advice builder and scheduler touch."""

    repository: SqliteObservationAdviceSemanticRepository
    gaps: tuple[str, ...] = _GAPS
    failed_identity: str = "hook:fail"

    def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]:
        assert workspace == _COMMITMENT
        return (
            _envelope(
                self.failed_identity,
                {"tool_name": "shell", "exit_status": 1, "correlation_id": self.failed_identity},
            ),
        )

    async def status(self, query: ObservationStatusQuery) -> ObservationStatus:
        assert query.workspace_commitment == _COMMITMENT
        return ObservationStatus(
            ObservationLifecycle.ACTIVE, _COMMITMENT, {}, _TIME, 0, self.gaps, (), None
        )

    def load_advice_snapshot(self, workspace: str) -> None:
        assert workspace == _COMMITMENT
        return None

    def advice_semantic_repository(self) -> SqliteObservationAdviceSemanticRepository:
        return self.repository


def _clock() -> str:
    return "2026-09-08T21:00:00.000Z"


def _later() -> str:
    return "2026-09-08T21:02:00.000Z"


def _builder() -> ObservationAdviceContextBuilder:
    return ObservationAdviceContextBuilder(
        semantic_scheduler=ObservationAdviceSemanticScheduler(now=_clock)
    )


def _rows(
    db: apsw.Connection, repository: SqliteObservationAdviceSemanticRepository
) -> tuple[ObservationAdviceSemanticAttempt, ...]:
    """Every durable row in schedule order. The row is keyed by the pre-semantic evidence basis;
    the snapshot's own basis digest is recomputed after the semantic gap is folded in."""

    keys = db.execute(
        "SELECT yoetz_session_id, basis_digest FROM observation_advice_semantic_attempts "
        "ORDER BY state_token"
    ).fetchall()
    rows: list[ObservationAdviceSemanticAttempt] = []
    for session, basis in keys:
        row = repository.lookup(yoetz_session_id=str(session), basis_digest=str(basis))
        assert row is not None
        rows.append(row)
    return tuple(rows)


def _packet(attempt: ObservationAdviceSemanticAttempt) -> Mapping[str, object]:
    parsed = strict_json_parse(attempt.packet_json)
    assert isinstance(parsed, Mapping)
    return parsed


def test_hook_path_build_enqueues_once_and_never_dispatches() -> None:
    """Two identical advice builds produce one durable row and a pending gap, no provider call.

    The scoped observation gaps reach the row and the packet byte-for-byte; the old inline
    callback dropped them and built the packet with an empty gap tuple.
    """

    db, repository = _repository()
    store = _Store(repository)
    builder = _builder()

    first = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    second = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]

    assert first is not None and second is not None
    assert first.evidence_basis_digest == second.evidence_basis_digest
    assert ADVICE_SEMANTIC_PENDING_GAP in first.confidence_coverage.known_gaps
    assert ADVICE_SEMANTIC_PENDING_GAP in second.confidence_coverage.known_gaps
    assert CheckType.SEMANTIC_MODEL_DERIVED not in first.confidence_coverage.check_types
    assert repository.list_pending_workspaces() == (_COMMITMENT,)
    (attempt,) = _rows(db, repository)
    assert attempt.status == "pending"
    assert attempt.attempt_count == 0
    assert attempt.yoetz_session_id == _SESSION
    assert attempt.coverage_gaps == tuple(sorted(_GAPS, key=str.encode))
    assert _packet(attempt)["coverage_gaps"] == list(sorted(_GAPS, key=str.encode))
    assert _packet(attempt)["evidence_basis_digest"] == attempt.basis_digest


def test_changed_basis_schedules_a_new_attempt_and_retains_the_prior_receipt() -> None:
    db, repository = _repository()
    store = _Store(repository)
    builder = _builder()
    first = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    assert first is not None
    (first_row,) = _rows(db, repository)

    # Complete the first attempt through the worker so it holds a receipt.
    async def dispatch(
        attempt: ObservationAdviceSemanticAttempt,
    ) -> ObservationAdviceSemanticOutcome:
        return ObservationAdviceSemanticOutcome(
            status="succeeded",
            attempt_receipt="egr_first",
            provider_identity="provider-a",
            evidence_digest=attempt.subject_digest,
        )

    worker = ObservationAdviceSemanticWorker(
        repository=repository,
        dispatch=dispatch,
        service_generation=1,
        lease_owner="svc-1",
        now=_clock,
        lease_expires_at=_later,
    )
    assert asyncio.run(worker.run_once()) is not None
    assert asyncio.run(worker.run_once()) is None

    # New evidence changes the basis: a second row is scheduled, the first keeps its receipt,
    # and the rebuilt advice over the old basis still reads the succeeded row.
    store.failed_identity = "hook:fail-2"
    second = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    assert second is not None
    assert second.evidence_basis_digest != first.evidence_basis_digest
    assert ADVICE_SEMANTIC_PENDING_GAP in second.confidence_coverage.known_gaps
    retained, scheduled = _rows(db, repository)
    assert retained.basis_digest == first_row.basis_digest
    assert (retained.status, retained.attempt_receipt) == ("succeeded", "egr_first")
    assert scheduled.basis_digest != retained.basis_digest
    assert scheduled.status == "pending"
    # Rebuilding over the original evidence reads the succeeded row: no pending or unavailable
    # gap, and no re-dispatch.
    replay = asyncio.run(builder.build(_COMMITMENT, _Store(repository), yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    assert replay is not None
    assert ADVICE_SEMANTIC_PENDING_GAP not in replay.confidence_coverage.known_gaps
    assert ADVICE_SEMANTIC_UNAVAILABLE_GAP not in replay.confidence_coverage.known_gaps
    assert len(_rows(db, repository)) == 2


def test_unattempted_pending_rows_are_superseded_by_a_newer_basis() -> None:
    """A pending row the provider never saw is cancelled when the basis moves on, so the worker
    never reviews evidence the advice no longer stands on; the cancellation stays visible."""

    _db, repository = _repository()
    first = repository.schedule(
        workspace=_COMMITMENT,
        yoetz_session_id=_SESSION,
        basis_digest="sha256:" + "1" * 64,
        subject_digest="sha256:" + "1" * 64,
        coverage_gaps=_GAPS,
        packet_json=b"{}",
        enqueued_at=_clock(),
        max_pending=16,
    )
    second = repository.schedule(
        workspace=_COMMITMENT,
        yoetz_session_id=_SESSION,
        basis_digest="sha256:" + "2" * 64,
        subject_digest="sha256:" + "2" * 64,
        coverage_gaps=_GAPS,
        packet_json=b"{}",
        enqueued_at=_clock(),
        max_pending=16,
    )
    assert second.status == "pending"
    superseded = repository.lookup(yoetz_session_id=_SESSION, basis_digest=first.basis_digest)
    assert superseded is not None
    assert (superseded.status, superseded.failure_reason) == ("cancelled", "superseded")
    addon = addon_from_attempt(superseded)
    assert addon is not None and addon.finding_ids == () and addon.failure_reason == "superseded"


def test_queue_bound_records_unavailable_without_an_attempt() -> None:
    _db, repository = _repository()
    for index in range(2):
        session = f"ses_00000000-0000-4000-8000-00000000000{index + 2}"
        row = repository.schedule(
            workspace=_COMMITMENT,
            yoetz_session_id=session,
            basis_digest="sha256:" + str(index) * 64,
            subject_digest="sha256:" + str(index) * 64,
            coverage_gaps=(),
            packet_json=b"{}",
            enqueued_at=_clock(),
            max_pending=2,
        )
        assert row.status == "pending"
    overflow = repository.schedule(
        workspace=_COMMITMENT,
        yoetz_session_id=_SESSION,
        basis_digest="sha256:" + "f" * 64,
        subject_digest="sha256:" + "f" * 64,
        coverage_gaps=(),
        packet_json=b"{}",
        enqueued_at=_clock(),
        max_pending=2,
    )
    assert (overflow.status, overflow.failure_reason) == ("unavailable", "queue_full")
    assert overflow.attempt_count == 0
    addon = addon_from_attempt(overflow)
    assert addon is not None
    assert addon.failure_reason == "queue_full" and addon.finding_ids == ()
    # The bounded row never becomes claimable work.
    claimed = repository.claim_next(
        service_generation=1, lease_owner="svc", lease_expires_at=_later(), now=_clock()
    )
    assert claimed is not None and claimed.yoetz_session_id != _SESSION


@pytest.mark.parametrize(
    ("outcome", "expected_reason"),
    [
        (
            ObservationAdviceSemanticOutcome(
                status="unavailable", failure_reason="authorization_missing"
            ),
            "authorization_missing",
        ),
        (
            ObservationAdviceSemanticOutcome(
                status="unavailable", failure_reason="provider_unavailable"
            ),
            "provider_unavailable",
        ),
        (
            ObservationAdviceSemanticOutcome(status="failed", failure_reason="provider_failed"),
            "provider_failed",
        ),
    ],
)
def test_non_success_outcomes_leave_a_truthful_gap_and_no_semantic_claim(
    outcome: ObservationAdviceSemanticOutcome, expected_reason: str
) -> None:
    db, repository = _repository()
    store = _Store(repository)
    builder = _builder()
    pending = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    assert pending is not None

    async def dispatch(
        _attempt: ObservationAdviceSemanticAttempt,
    ) -> ObservationAdviceSemanticOutcome:
        return outcome

    worker = ObservationAdviceSemanticWorker(
        repository=repository,
        dispatch=dispatch,
        service_generation=1,
        lease_owner="svc-1",
        now=_clock,
        lease_expires_at=_later,
    )
    assert asyncio.run(worker.run_once()) is not None
    (row,) = _rows(db, repository)
    assert row.failure_reason == expected_reason and row.finding_ids == ()
    rebuilt = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    assert rebuilt is not None
    assert ADVICE_SEMANTIC_UNAVAILABLE_GAP in rebuilt.confidence_coverage.known_gaps
    assert ADVICE_SEMANTIC_PENDING_GAP not in rebuilt.confidence_coverage.known_gaps
    assert CheckType.SEMANTIC_MODEL_DERIVED not in rebuilt.confidence_coverage.check_types


def test_dispatch_exception_and_cancellation_are_recorded_not_succeeded() -> None:
    _db, repository = _repository()
    for basis, exc in (("sha256:" + "a" * 64, RuntimeError("boom")), ("sha256:" + "b" * 64, None)):
        session = _SESSION if basis.endswith("a" * 64) else _SESSION[:-1] + "2"
        repository.schedule(
            workspace=_COMMITMENT,
            yoetz_session_id=session,
            basis_digest=basis,
            subject_digest=basis,
            coverage_gaps=(),
            packet_json=b"{}",
            enqueued_at=_clock(),
            max_pending=16,
        )

        async def dispatch(
            _attempt: ObservationAdviceSemanticAttempt,
        ) -> ObservationAdviceSemanticOutcome:
            if exc is not None:
                raise exc
            raise asyncio.CancelledError

        worker = ObservationAdviceSemanticWorker(
            repository=repository,
            dispatch=dispatch,
            service_generation=1,
            lease_owner="svc-1",
            now=_clock,
            lease_expires_at=_later,
        )
        if exc is None:
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(worker.run_once())
        else:
            asyncio.run(worker.run_once())
        row = repository.lookup(yoetz_session_id=session, basis_digest=basis)
        assert row is not None
        assert row.status in {"failed", "cancelled"}
        assert row.failure_reason in ADVICE_SEMANTIC_FAILURE_REASONS
        assert row.finding_ids == ()


def test_restart_reclaims_a_foreign_generation_lease_without_reporting_success() -> None:
    """A row left running by a previous service generation is re-attempted, never succeeded."""

    _db, repository = _repository()
    basis = "sha256:" + "c" * 64
    repository.schedule(
        workspace=_COMMITMENT,
        yoetz_session_id=_SESSION,
        basis_digest=basis,
        subject_digest=basis,
        coverage_gaps=_GAPS,
        packet_json=b"{}",
        enqueued_at=_clock(),
        max_pending=16,
    )
    stranded = repository.claim_next(
        service_generation=1, lease_owner="svc-old", lease_expires_at=_later(), now=_clock()
    )
    assert stranded is not None and stranded.status == "running"
    # While the old generation holds the lease, its own claim finds nothing more to do and the
    # row is still not a success.
    assert (
        repository.claim_next(
            service_generation=1, lease_owner="svc-old", lease_expires_at=_later(), now=_clock()
        )
        is None
    )
    addon = addon_from_attempt(repository.lookup(yoetz_session_id=_SESSION, basis_digest=basis))
    assert addon is not None and addon.failure_reason == "pending" and addon.finding_ids == ()

    # The next generation reclaims it and the attempt count reflects the interrupted try.
    reclaimed = repository.claim_next(
        service_generation=2, lease_owner="svc-new", lease_expires_at=_later(), now=_clock()
    )
    assert reclaimed is not None
    assert reclaimed.attempt_id == stranded.attempt_id
    assert reclaimed.attempt_count == 2
    # The stale holder can no longer complete it.
    with pytest.raises(Exception, match="stale"):
        repository.complete(
            attempt=stranded,
            service_generation=1,
            lease_owner="svc-old",
            outcome=ObservationAdviceSemanticOutcome(status="succeeded", attempt_receipt="x"),
            recorded_at=_clock(),
        )
    repository.complete(
        attempt=reclaimed,
        service_generation=2,
        lease_owner="svc-new",
        outcome=ObservationAdviceSemanticOutcome(status="succeeded", attempt_receipt="egr_new"),
        recorded_at=_clock(),
    )
    done = repository.lookup(yoetz_session_id=_SESSION, basis_digest=basis)
    assert done is not None and (done.status, done.attempt_receipt) == ("succeeded", "egr_new")


def test_repeatedly_interrupted_rows_terminate_as_interrupted() -> None:
    _db, repository = _repository()
    basis = "sha256:" + "d" * 64
    repository.schedule(
        workspace=_COMMITMENT,
        yoetz_session_id=_SESSION,
        basis_digest=basis,
        subject_digest=basis,
        coverage_gaps=(),
        packet_json=b"{}",
        enqueued_at=_clock(),
        max_pending=16,
    )
    for generation in (1, 2, 3):
        claimed = repository.claim_next(
            service_generation=generation,
            lease_owner=f"svc-{generation}",
            lease_expires_at=_later(),
            now=_clock(),
        )
        assert claimed is not None
    assert (
        repository.claim_next(
            service_generation=4, lease_owner="svc-4", lease_expires_at=_later(), now=_clock()
        )
        is None
    )
    row = repository.lookup(yoetz_session_id=_SESSION, basis_digest=basis)
    assert row is not None
    assert (row.status, row.failure_reason) == ("failed", "interrupted")


def test_supervisor_drains_registered_workers_and_reruns_advice_after_each_attempt() -> None:
    db, repository = _repository()
    store = _Store(repository)
    builder = _builder()
    pending = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    assert pending is not None
    (row,) = _rows(db, repository)
    dispatched: list[str] = []
    reruns: list[str] = []
    idle: list[str] = []

    async def dispatch(
        attempt: ObservationAdviceSemanticAttempt,
    ) -> ObservationAdviceSemanticOutcome:
        dispatched.append(attempt.basis_digest)
        return ObservationAdviceSemanticOutcome(
            status="succeeded",
            attempt_receipt="egr_ok",
            provider_identity="provider-a",
            evidence_digest=attempt.subject_digest,
        )

    async def after() -> None:
        reruns.append("advice")

    async def on_idle() -> None:
        idle.append("released")

    async def scenario() -> None:
        supervisor = ObservationAdviceSemanticSupervisor(service_generation=1)
        worker = ObservationAdviceSemanticWorker(
            repository=repository,
            dispatch=dispatch,
            service_generation=1,
            lease_owner="svc-1",
            now=_clock,
            lease_expires_at=_later,
        )
        assert supervisor.register(
            AdviceSemanticDrainHandle(_COMMITMENT, worker, after_complete=after, on_idle=on_idle)
        )
        await supervisor.drain_once()
        assert not supervisor.has_handle(_COMMITMENT)
        await supervisor.stop()

    asyncio.run(scenario())
    assert dispatched == [row.basis_digest]
    assert reruns == ["advice"] and idle == ["released"]
    rebuilt = asyncio.run(builder.build(_COMMITMENT, store, yoetz_session_id=_SESSION))  # type: ignore[arg-type]
    assert rebuilt is not None
    assert ADVICE_SEMANTIC_PENDING_GAP not in rebuilt.confidence_coverage.known_gaps
    assert ADVICE_SEMANTIC_UNAVAILABLE_GAP not in rebuilt.confidence_coverage.known_gaps
    # A succeeded attempt without challenges is an honest receipt, not an additive finding.
    assert CheckType.SEMANTIC_MODEL_DERIVED not in rebuilt.confidence_coverage.check_types


def test_outcome_shape_is_closed() -> None:
    with pytest.raises(ValueError, match="advice_semantic_outcome_invalid"):
        ObservationAdviceSemanticOutcome(status="failed", failure_reason="made_up")
    with pytest.raises(ValueError, match="advice_semantic_outcome_invalid"):
        ObservationAdviceSemanticOutcome(status="succeeded", failure_reason="provider_failed")
    with pytest.raises(ValueError, match="advice_semantic_outcome_invalid"):
        ObservationAdviceSemanticOutcome(
            status="failed", failure_reason="provider_failed", finding_ids=("fnd_x",)
        )
