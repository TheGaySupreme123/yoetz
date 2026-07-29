"""Unit tests for durable semantic attempt budget, retry matrix, and accounting."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal, cast

import pytest

from yoetz.application.semantic_attempts import (
    SemanticAttemptAccounting,
    attempt_accounting_from_rows,
    attempt_accounting_to_json,
    final_status_after_exhaustion,
    is_retriable_semantic_outcome,
    physical_attempt_budget,
    run_durable_semantic_attempts,
    should_retry_after,
)
from yoetz.domain.values import Frontier
from yoetz.ports.ledger import (
    AttemptOutcome,
    CheckPhase,
    OperationLease,
    SemanticAttemptHandle,
    SemanticAttemptRecord,
    SemanticJobRecord,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef
from yoetz.ports.semantic import Deadline
from yoetz.protocol.models import SemanticReason, SemanticStatus

_JOB = "job_40000000-0000-4000-8000-000000000001"
_ATT1 = "att_40000000-0000-4000-8000-000000000001"
_ATT2 = "att_40000000-0000-4000-8000-000000000002"
_ATT3 = "att_40000000-0000-4000-8000-000000000003"
_WRITER = "wri_40000000-0000-4000-8000-000000000001"
_OP = "req_40000000-0000-4000-8000-000000000001"
_SESSION = "ses_40000000-0000-4000-8000-000000000001"
_CASE = "sha256:" + "a" * 64
_DEP = "sha256:" + "b" * 64
_FRONTIER = Frontier(1, "sha256:" + "c" * 64)


def _case_ref() -> ObjectRef:
    return ObjectRef(
        "obj_40000000-0000-4000-8000-000000000001",
        2,
        "hmac-sha256:" + "d" * 64,
        "sha256:" + "e" * 64,
        "yoetz-object/1",
        "bmk-1",
        ObjectMetadata(
            ObjectKind.SEMANTIC_CASE,
            "application/json",
            "tsk_40000000-0000-4000-8000-000000000001",
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def _response_ref() -> ObjectRef:
    return ObjectRef(
        "obj_40000000-0000-4000-8000-000000000002",
        2,
        "hmac-sha256:" + "f" * 64,
        "sha256:" + "1" * 64,
        "yoetz-object/1",
        "bmk-1",
        ObjectMetadata(
            ObjectKind.SEMANTIC_RESPONSE,
            "application/json",
            "tsk_40000000-0000-4000-8000-000000000001",
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )


def _lease() -> OperationLease:
    return OperationLease(
        _WRITER,
        _OP,
        _SESSION,
        CheckPhase.SEMANTIC_WAIT,
        "owner-generation-1",
        "lease-owner-1",
        1,
        datetime(2030, 1, 1, tzinfo=UTC),
        _FRONTIER,
        _DEP,
    )


def test_physical_attempt_budget_caps_at_adr_006() -> None:
    assert physical_attempt_budget(0) == 1
    assert physical_attempt_budget(1) == 2
    assert physical_attempt_budget(2) == 3
    assert physical_attempt_budget(99) == 3


def test_retry_matrix_admits_only_approved_transient_classes() -> None:
    assert is_retriable_semantic_outcome(SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT)
    assert is_retriable_semantic_outcome(
        SemanticStatus.UNAVAILABLE, SemanticReason.TRANSPORT_UNAVAILABLE
    )
    assert is_retriable_semantic_outcome(
        SemanticStatus.UNAVAILABLE, SemanticReason.PROVIDER_RATE_LIMITED
    )
    assert not is_retriable_semantic_outcome(
        SemanticStatus.INVALID, SemanticReason.RESPONSE_SCHEMA_INVALID
    )
    assert not is_retriable_semantic_outcome(
        SemanticStatus.REFUSED, SemanticReason.PROVIDER_REFUSED
    )
    assert not is_retriable_semantic_outcome(
        SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.NETWORK_EGRESS_DENIED
    )
    assert not is_retriable_semantic_outcome(
        SemanticStatus.HUMAN_DENIED, SemanticReason.HUMAN_DENIED
    )
    assert not is_retriable_semantic_outcome(
        SemanticStatus.UNAVAILABLE, SemanticReason.PROVIDER_QUOTA_EXHAUSTED
    )
    assert not is_retriable_semantic_outcome(SemanticStatus.STALE, SemanticReason.FRONTIER_CHANGED)


def test_should_retry_respects_budget_and_deadline() -> None:
    assert should_retry_after(
        status=SemanticStatus.TIMEOUT,
        reason=SemanticReason.PROVIDER_TIMEOUT,
        attempts_completed=1,
        max_retries=2,
        deadline_expired=False,
    )
    assert not should_retry_after(
        status=SemanticStatus.TIMEOUT,
        reason=SemanticReason.PROVIDER_TIMEOUT,
        attempts_completed=1,
        max_retries=0,
        deadline_expired=False,
    )
    assert not should_retry_after(
        status=SemanticStatus.TIMEOUT,
        reason=SemanticReason.PROVIDER_TIMEOUT,
        attempts_completed=3,
        max_retries=2,
        deadline_expired=False,
    )
    assert not should_retry_after(
        status=SemanticStatus.TIMEOUT,
        reason=SemanticReason.PROVIDER_TIMEOUT,
        attempts_completed=1,
        max_retries=2,
        deadline_expired=True,
    )


def test_final_status_after_exhaustion() -> None:
    status, reason = final_status_after_exhaustion(
        SemanticStatus.TIMEOUT,
        SemanticReason.PROVIDER_TIMEOUT,
        attempts_completed=1,
        max_retries=0,
    )
    assert (status, reason) == (SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT)

    status, reason = final_status_after_exhaustion(
        SemanticStatus.TIMEOUT,
        SemanticReason.PROVIDER_TIMEOUT,
        attempts_completed=3,
        max_retries=2,
    )
    assert (status, reason) == (
        SemanticStatus.UNAVAILABLE,
        SemanticReason.RETRY_BUDGET_EXHAUSTED,
    )


def test_attempt_accounting_from_rows_is_structural() -> None:
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "failed",
        2,
        None,
        None,
        None,
        None,
        None,
        None,
        SemanticReason.RETRY_BUDGET_EXHAUSTED,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    attempts = (
        SemanticAttemptRecord(
            _JOB,
            _ATT1,
            1,
            "req_40000000-0000-4000-8000-0000000000aa",
            "expired",
            SemanticReason.PROVIDER_TIMEOUT,
            None,
        ),
        SemanticAttemptRecord(
            _JOB,
            _ATT2,
            2,
            "req_40000000-0000-4000-8000-0000000000bb",
            "failed",
            SemanticReason.RETRY_BUDGET_EXHAUSTED,
            None,
        ),
    )
    accounting = attempt_accounting_from_rows(job, attempts, max_retries=1)
    assert accounting.attempted_count == 2
    assert accounting.selected_attempt_id is None
    assert accounting.exhausted is True
    encoded = attempt_accounting_to_json(accounting)
    # No user-controlled content: only structural tokens and counts.
    assert encoded["attempted_count"] == 2
    assert encoded["exhausted"] is True
    raw = str(encoded)
    assert "timeout" in raw or "provider_timeout" in raw
    assert "secret" not in raw
    assert "prompt" not in raw


@dataclass(frozen=True, slots=True)
class _Eval:
    status: SemanticStatus
    reason: SemanticReason
    judgment: object | None = None
    provenance: object | None = None


@dataclass
class _FakeLedger:
    job: SemanticJobRecord
    lease: OperationLease
    outcomes: list[tuple[str, AttemptOutcome, SemanticReason | None]] | None = None
    dispatches: list[str] | None = None
    mono: float = 0.0
    attempt_ids: list[str] | None = None
    provider_ids: list[str] | None = None
    selected: str | None = None
    attempts: dict[str, SemanticAttemptRecord] | None = None
    renew_count: int = 0
    claim_calls: int = 0

    def __post_init__(self) -> None:
        self.outcomes = []
        self.dispatches = []
        self.attempt_ids = []
        self.provider_ids = []
        self.attempts = {}
        self.renew_count = 0
        self.claim_calls = 0

    async def renew_leases(self, lease: OperationLease) -> OperationLease:
        assert lease.writer_id == self.lease.writer_id
        assert lease.operation_id == self.lease.operation_id
        self.renew_count += 1
        self.lease = replace(
            self.lease,
            lease_generation=self.lease.lease_generation + 1,
            lease_expires_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
        return self.lease

    async def claim_semantic_job(self, lease: OperationLease, job_id: str) -> SemanticAttemptHandle:
        # After terminal recovery, claim must not be invoked.
        if self.job.state in {"succeeded", "failed", "quarantined"}:
            raise AssertionError("claim_semantic_job_on_terminal_job")
        assert lease == self.lease
        assert job_id == self.job.job_id
        self.claim_calls += 1
        ordinal = self.job.attempt_count + 1
        att = f"att_40000000-0000-4000-8000-{ordinal:012x}"
        req = f"req_40000000-0000-4000-8000-{ordinal:012x}"
        handle = SemanticAttemptHandle(
            job_id,
            att,
            ordinal,
            req,
            lease.writer_id,
            lease.operation_id,
            lease.owner_generation,
            lease.lease_owner_id,
            ordinal,
            lease.lease_expires_at,
            lease.frontier,
            lease.dependency_digest,
        )
        self.job = replace(
            self.job,
            state="leased",
            attempt_count=ordinal,
            active_attempt_id=att,
            lease_owner_id=lease.lease_owner_id,
            lease_generation=ordinal,
            lease_expires_at=lease.lease_expires_at,
        )
        assert self.attempt_ids is not None
        assert self.provider_ids is not None
        assert self.attempts is not None
        self.attempt_ids.append(att)
        self.provider_ids.append(req)
        self.attempts[att] = SemanticAttemptRecord(job_id, att, ordinal, req, "started", None, None)
        return handle

    async def record_attempt_outcome(
        self,
        handle: SemanticAttemptHandle,
        outcome: AttemptOutcome,
        result_object_ref: ObjectRef | None = None,
        terminal_code: SemanticReason | None = None,
    ) -> None:
        assert self.attempts is not None
        assert self.outcomes is not None
        self.outcomes.append((handle.attempt_id, outcome, terminal_code))
        state: Literal["started", "response_durable", "selected", "failed", "expired", "late"]
        if outcome is AttemptOutcome.RESPONSE_DURABLE:
            state = "response_durable"
        elif outcome is AttemptOutcome.EXPIRED:
            state = "expired"
        elif outcome is AttemptOutcome.FAILED:
            state = "failed"
        elif outcome is AttemptOutcome.LATE:
            state = "late"
        else:
            state = "selected"
        self.attempts[handle.attempt_id] = SemanticAttemptRecord(
            handle.job_id,
            handle.attempt_id,
            handle.attempt_ordinal,
            handle.provider_request_id,
            state,
            terminal_code,
            result_object_ref,
        )
        if outcome is AttemptOutcome.EXPIRED:
            self.job = replace(
                self.job,
                state="queued",
                active_attempt_id=None,
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
            )
        elif outcome is AttemptOutcome.FAILED:
            self.job = replace(
                self.job,
                state="failed",
                active_attempt_id=None,
                lease_owner_id=None,
                lease_generation=None,
                lease_expires_at=None,
                terminal_code=terminal_code,
                terminal_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

    async def fail_semantic_job(
        self,
        lease: OperationLease,
        job_id: str,
        terminal_code: SemanticReason,
    ) -> SemanticJobRecord:
        assert lease == self.lease
        assert job_id == self.job.job_id
        assert self.job.state == "queued"
        self.job = replace(
            self.job,
            state="failed",
            terminal_code=terminal_code,
            terminal_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return self.job

    async def select_attempt(
        self,
        lease: OperationLease,
        handle: SemanticAttemptHandle,
        selected_result_object_ref: ObjectRef,
    ) -> object:
        self.selected = handle.attempt_id
        self.job = replace(
            self.job,
            state="succeeded",
            active_attempt_id=None,
            selected_attempt_id=handle.attempt_id,
            selected_result_object_ref=selected_result_object_ref,
            terminal_code=SemanticReason.SEMANTIC_COMPLETED,
            terminal_at=datetime(2026, 1, 1, tzinfo=UTC),
            lease_owner_id=None,
            lease_generation=None,
            lease_expires_at=None,
        )
        return object()

    async def load_semantic_job(
        self, writer_id: str, operation_id: str
    ) -> SemanticJobRecord | None:
        assert (writer_id, operation_id) == (_WRITER, _OP)
        return self.job

    async def list_semantic_attempts(self, job_id: str) -> tuple[SemanticAttemptRecord, ...]:
        assert job_id == _JOB
        assert self.attempts is not None
        return tuple(sorted(self.attempts.values(), key=lambda item: item.attempt_ordinal))


@pytest.mark.anyio
async def test_zero_retry_performs_exactly_one_attempt() -> None:
    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    ledger = _FakeLedger(job, lease)
    script = [
        _Eval(SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT),
    ]

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        assert ledger.dispatches is not None
        ledger.dispatches.append(handle.attempt_id)
        assert not deadline.expired(0.0)
        return script.pop(0)

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        raise AssertionError("publish_not_expected")

    finals: list[tuple[SemanticStatus, SemanticReason, int]] = []

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        finals.append((status, reason, accounting.attempted_count))
        return (status, reason, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2030, 1, 1, tzinfo=UTC), 1000.0),
            max_retries=0,
            now_monotonic=lambda: 0.0,
            dispatch=dispatch,
            publish_success_response=publish,
            sleep=lambda _: _async_noop(),
            build_final=build_final,
        ),
    )
    assert ledger.dispatches is not None and len(ledger.dispatches) == 1
    assert ledger.outcomes is not None and ledger.outcomes[0][1] is AttemptOutcome.FAILED
    assert finals[0] == (SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT, 1)
    assert result[0] is SemanticStatus.TIMEOUT


async def _async_noop() -> None:
    return None


@pytest.mark.anyio
async def test_total_deadline_before_first_attempt_terminally_fails_job() -> None:
    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    ledger = _FakeLedger(job, lease)

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        raise AssertionError("dispatch_not_expected_after_total_deadline")

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        raise AssertionError("publish_not_expected")

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2026, 1, 1, tzinfo=UTC), 0.0),
            max_retries=2,
            now_monotonic=lambda: 1.0,
            dispatch=dispatch,
            publish_success_response=publish,
            build_final=build_final,
        ),
    )
    assert result[0] is SemanticStatus.TIMEOUT
    assert result[1] is SemanticReason.PROVIDER_TIMEOUT
    assert result[2].attempted_count == 0
    assert ledger.job.state == "failed"
    assert ledger.job.terminal_code is SemanticReason.PROVIDER_TIMEOUT


@pytest.mark.anyio
async def test_total_deadline_after_retriable_attempt_terminally_fails_queued_job() -> None:
    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    ledger = _FakeLedger(job, lease)
    monotonic_values = iter((0.0, 0.0, 0.0, 0.0, 1.0, 1.0))

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        assert ledger.dispatches is not None
        ledger.dispatches.append(handle.attempt_id)
        return _Eval(SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT)

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        raise AssertionError("publish_not_expected")

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2026, 1, 1, tzinfo=UTC), 0.5),
            max_retries=2,
            now_monotonic=lambda: next(monotonic_values),
            dispatch=dispatch,
            publish_success_response=publish,
            build_final=build_final,
        ),
    )
    assert result[0] is SemanticStatus.TIMEOUT
    assert result[1] is SemanticReason.PROVIDER_TIMEOUT
    assert result[2].attempted_count == 1
    assert ledger.outcomes == [
        (_ATT1, AttemptOutcome.EXPIRED, SemanticReason.PROVIDER_TIMEOUT)
    ]
    assert ledger.job.state == "failed"
    assert ledger.job.terminal_code is SemanticReason.PROVIDER_TIMEOUT


@pytest.mark.anyio
async def test_two_retries_perform_at_most_three_physical_attempts() -> None:
    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    ledger = _FakeLedger(job, lease)
    script = [
        _Eval(SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT),
        _Eval(SemanticStatus.UNAVAILABLE, SemanticReason.PROVIDER_RATE_LIMITED),
        _Eval(SemanticStatus.UNAVAILABLE, SemanticReason.TRANSPORT_UNAVAILABLE),
        _Eval(SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT),  # must not run
    ]

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        assert ledger.dispatches is not None
        ledger.dispatches.append(handle.provider_request_id)
        return script.pop(0)

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        raise AssertionError("publish_not_expected")

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2030, 1, 1, tzinfo=UTC), 1000.0),
            max_retries=2,
            now_monotonic=lambda: 0.0,
            dispatch=dispatch,
            publish_success_response=publish,
            sleep=lambda _: _async_noop(),
            build_final=build_final,
        ),
    )
    assert ledger.dispatches is not None and len(ledger.dispatches) == 3
    assert len(set(ledger.dispatches)) == 3  # unique provider request ids
    assert ledger.attempt_ids is not None and len(set(ledger.attempt_ids)) == 3
    assert result[0] is SemanticStatus.UNAVAILABLE
    assert result[1] is SemanticReason.RETRY_BUDGET_EXHAUSTED
    assert result[2].attempted_count == 3
    assert result[2].exhausted is True
    # Intermediate retriable outcomes used EXPIRED; final FAILED.
    assert ledger.outcomes is not None
    assert [item[1] for item in ledger.outcomes] == [
        AttemptOutcome.EXPIRED,
        AttemptOutcome.EXPIRED,
        AttemptOutcome.FAILED,
    ]


@pytest.mark.anyio
async def test_success_selects_first_valid_and_stops() -> None:
    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    ledger = _FakeLedger(job, lease)
    script = [
        _Eval(SemanticStatus.TIMEOUT, SemanticReason.PROVIDER_TIMEOUT),
        _Eval(SemanticStatus.SUCCEEDED, SemanticReason.SEMANTIC_COMPLETED),
        _Eval(SemanticStatus.SUCCEEDED, SemanticReason.SEMANTIC_COMPLETED),
    ]

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        assert ledger.dispatches is not None
        ledger.dispatches.append(handle.attempt_id)
        return script.pop(0)

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        return _response_ref()

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2030, 1, 1, tzinfo=UTC), 1000.0),
            max_retries=2,
            now_monotonic=lambda: 0.0,
            dispatch=dispatch,
            publish_success_response=publish,
            sleep=lambda _: _async_noop(),
            build_final=build_final,
        ),
    )
    assert ledger.dispatches is not None
    assert len(ledger.dispatches) == 2
    assert ledger.selected is not None
    assert result[0] is SemanticStatus.SUCCEEDED
    assert result[2].selected_attempt_id == ledger.selected
    assert result[2].attempted_count == 2


@pytest.mark.anyio
async def test_policy_block_never_retries() -> None:
    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    ledger = _FakeLedger(job, lease)

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        assert ledger.dispatches is not None
        ledger.dispatches.append(handle.attempt_id)
        return _Eval(SemanticStatus.BLOCKED_BY_POLICY, SemanticReason.NETWORK_EGRESS_DENIED)

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        raise AssertionError("publish_not_expected")

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2030, 1, 1, tzinfo=UTC), 1000.0),
            max_retries=2,
            now_monotonic=lambda: 0.0,
            dispatch=dispatch,
            publish_success_response=publish,
            sleep=lambda _: _async_noop(),
            build_final=build_final,
        ),
    )
    assert ledger.dispatches is not None and len(ledger.dispatches) == 1
    assert result[0] is SemanticStatus.BLOCKED_BY_POLICY
    assert ledger.outcomes is not None and ledger.outcomes[0][1] is AttemptOutcome.FAILED


@pytest.mark.anyio
async def test_terminal_failed_job_recovers_without_claim() -> None:
    """Crash after final FAILED, before check commit: replay must not re-claim."""

    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "failed",
        1,
        None,
        None,
        None,
        None,
        None,
        None,
        SemanticReason.PROVIDER_TIMEOUT,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    ledger = _FakeLedger(job, lease)
    ledger.attempts = {
        _ATT1: SemanticAttemptRecord(
            _JOB,
            _ATT1,
            1,
            "req_40000000-0000-4000-8000-0000000000aa",
            "failed",
            SemanticReason.PROVIDER_TIMEOUT,
            None,
        )
    }

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        raise AssertionError("dispatch_not_expected_on_terminal_recovery")

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        raise AssertionError("publish_not_expected")

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2030, 1, 1, tzinfo=UTC), 1000.0),
            max_retries=2,
            now_monotonic=lambda: 0.0,
            dispatch=dispatch,
            publish_success_response=publish,
            sleep=lambda _: _async_noop(),
            build_final=build_final,
        ),
    )
    assert ledger.claim_calls == 0
    assert result[0] is SemanticStatus.TIMEOUT
    assert result[1] is SemanticReason.PROVIDER_TIMEOUT
    assert result[2].attempted_count == 1
    assert ledger.renew_count >= 1


@pytest.mark.anyio
async def test_terminal_succeeded_job_recovers_via_selected_callback() -> None:
    """Crash after select_attempt: recover judgment/provenance without re-claim."""

    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "succeeded",
        1,
        None,
        _ATT1,
        None,
        None,
        None,
        _response_ref(),
        SemanticReason.SEMANTIC_COMPLETED,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    ledger = _FakeLedger(job, lease)
    ledger.attempts = {
        _ATT1: SemanticAttemptRecord(
            _JOB,
            _ATT1,
            1,
            "req_40000000-0000-4000-8000-0000000000aa",
            "selected",
            SemanticReason.SEMANTIC_COMPLETED,
            _response_ref(),
        )
    }
    recovered = _Eval(SemanticStatus.SUCCEEDED, SemanticReason.SEMANTIC_COMPLETED, judgment="j")

    async def recover_selected(row: SemanticJobRecord) -> _Eval | None:
        assert row.state == "succeeded"
        return recovered

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        raise AssertionError("dispatch_not_expected_on_terminal_recovery")

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        raise AssertionError("publish_not_expected")

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, evaluation, accounting)

    result = cast(
        tuple[SemanticStatus, SemanticReason, object | None, SemanticAttemptAccounting],
        await run_durable_semantic_attempts(
            ledger=ledger,
            lease=lease,
            job=job,
            deadline=Deadline(datetime(2030, 1, 1, tzinfo=UTC), 1000.0),
            max_retries=2,
            now_monotonic=lambda: 0.0,
            dispatch=dispatch,
            publish_success_response=publish,
            sleep=lambda _: _async_noop(),
            build_final=build_final,
            recover_selected=recover_selected,
        ),
    )
    assert ledger.claim_calls == 0
    assert result[0] is SemanticStatus.SUCCEEDED
    assert result[2] is recovered
    assert result[3].selected_attempt_id == _ATT1


@pytest.mark.anyio
async def test_attempt_loop_renews_operation_lease() -> None:
    """Lease is renewed around claim/select so timeout_seconds can exceed the 60s TTL."""

    lease = _lease()
    job = SemanticJobRecord(
        _JOB,
        _WRITER,
        _OP,
        _CASE,
        _case_ref(),
        "queued",
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    ledger = _FakeLedger(job, lease)
    renewed: list[OperationLease] = []

    async def dispatch(handle: SemanticAttemptHandle, deadline: Deadline) -> _Eval:
        return _Eval(SemanticStatus.SUCCEEDED, SemanticReason.SEMANTIC_COMPLETED)

    async def publish(handle: SemanticAttemptHandle, evaluation: object) -> ObjectRef:
        return _response_ref()

    def build_final(
        status: SemanticStatus,
        reason: SemanticReason,
        evaluation: object | None,
        accounting: SemanticAttemptAccounting,
    ) -> object:
        return (status, reason, accounting)

    def on_lease(renewed_lease: OperationLease) -> None:
        renewed.append(renewed_lease)

    await run_durable_semantic_attempts(
        ledger=ledger,
        lease=lease,
        job=job,
        deadline=Deadline(datetime(2030, 1, 1, tzinfo=UTC), 1000.0),
        max_retries=0,
        now_monotonic=lambda: 0.0,
        dispatch=dispatch,
        publish_success_response=publish,
        sleep=lambda _: _async_noop(),
        build_final=build_final,
        on_lease_renewed=on_lease,
    )
    # At least: initial renew + pre-claim renew + post-dispatch renew.
    assert ledger.renew_count >= 3
    assert len(renewed) >= 3
    assert renewed[-1].lease_generation > lease.lease_generation
