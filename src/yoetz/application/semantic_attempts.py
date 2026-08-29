"""Durable semantic-operation attempt budget, retry matrix, and accounting.

ADR-006: one durable semantic operation, at most two retries (``max_retries``), one total
deadline (``timeout_seconds``), and one physical attempt identity per dispatch. The ledger's
``semantic_jobs`` / ``semantic_attempts`` tables are the recovery authority — never a
memory-only coordinator object.

Two retry classes share that budget. Transient classes (timeout, transport, rate limit) may use
every remaining slot. The repair class — a response that reached the provider and came back
``invalid / response_content_invalid`` (incomplete or overlong output) — may use exactly one slot
per job (issue #348), resubmitting the same frozen case as a fresh physical attempt. Every other
invalid, refusal, policy, human, stale, quota, and secret class is terminal on first sight.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, Protocol

from yoetz.observability.logging import record_unexpected_exception_without_raising
from yoetz.ports.ledger import (
    AttemptOutcome,
    OperationLease,
    SemanticAttemptHandle,
    SemanticAttemptRecord,
    SemanticJobRecord,
)
from yoetz.ports.objects import ObjectRef
from yoetz.ports.semantic import Deadline
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import PublicOperationError
from yoetz.protocol.models import VALID_SEMANTIC_REASONS, SemanticReason, SemanticStatus

__all__ = [
    "SemanticAttemptAccounting",
    "SemanticAttemptDispatch",
    "attempt_accounting_from_rows",
    "attempt_accounting_to_json",
    "backoff_seconds",
    "final_status_after_exhaustion",
    "is_repairable_semantic_outcome",
    "is_retriable_semantic_outcome",
    "max_physical_attempts",
    "physical_attempt_budget",
    "repair_retries_from_rows",
    "run_durable_semantic_attempts",
    "should_retry_after",
    "status_for_semantic_reason",
]

# ADR-006 approved transient classes only. Contract-invalid is not a transient class.
_RETRIABLE_REASONS: Final[frozenset[SemanticReason]] = frozenset(
    {
        SemanticReason.PROVIDER_TIMEOUT,
        SemanticReason.TRANSPORT_UNAVAILABLE,
        SemanticReason.PROVIDER_RATE_LIMITED,
    }
)

# Issue #348 repair class: the provider was reached and answered, but the answer was incomplete
# or overlong (``failure_class=response_content``). One bounded resubmission of the same frozen
# case may recover a usable judgment. ``response_schema_invalid`` (non-JSON / wrong shape) and
# ``semantic_judgment_rejected`` (post-validation) are deliberately absent: they are not fixed by
# asking again.
_REPAIRABLE_REASONS: Final[frozenset[SemanticReason]] = frozenset(
    {SemanticReason.RESPONSE_CONTENT_INVALID}
)

# At most one repair retry per durable semantic job, whatever ``max_retries`` allows.
_REPAIR_RETRY_LIMIT: Final = 1

_RETRIABLE_STATUSES: Final[frozenset[SemanticStatus]] = frozenset(
    {
        SemanticStatus.TIMEOUT,
        SemanticStatus.UNAVAILABLE,
    }
)

# Terminal job states that must not re-enter claim_semantic_job (recovery authority).
_TERMINAL_JOB_STATES: Final[frozenset[str]] = frozenset({"succeeded", "failed", "quarantined"})

# ADR-006: at most two retries → at most three physical attempts.
_ADR_MAX_RETRIES: Final = 2

# Reverse map: each closed SemanticReason belongs to exactly one SemanticStatus.
_STATUS_BY_REASON: Final[dict[SemanticReason, SemanticStatus]] = {
    reason: status for status, reasons in VALID_SEMANTIC_REASONS.items() for reason in reasons
}


@dataclass(frozen=True, slots=True)
class SemanticAttemptAccounting:
    """Bounded structural attempt accounting reconstructible from durable rows."""

    attempted_count: int
    selected_attempt_id: str | None
    exhausted: bool
    terminal_reason_counts: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if type(self.attempted_count) is not int or self.attempted_count < 0:
            raise ValueError("semantic_attempt_accounting_invalid")
        if self.selected_attempt_id is not None and (
            type(self.selected_attempt_id) is not str
            or not self.selected_attempt_id.startswith("att_")
        ):
            raise ValueError("semantic_attempt_accounting_invalid")
        if type(self.exhausted) is not bool:
            raise ValueError("semantic_attempt_accounting_invalid")
        if type(self.terminal_reason_counts) is not tuple:
            raise ValueError("semantic_attempt_accounting_invalid")
        for token, count in self.terminal_reason_counts:
            if type(token) is not str or type(count) is not int or count < 1:
                raise ValueError("semantic_attempt_accounting_invalid")


def physical_attempt_budget(max_retries: int) -> int:
    """Return the maximum physical attempts for a configured retry count (capped by ADR-006)."""

    if type(max_retries) is not int or max_retries < 0:
        raise ValueError("max_retries_invalid")
    capped = min(max_retries, _ADR_MAX_RETRIES)
    return 1 + capped


def max_physical_attempts(max_retries: int) -> int:
    """Alias used by tests and composition; same as :func:`physical_attempt_budget`."""

    return physical_attempt_budget(max_retries)


def is_retriable_semantic_outcome(status: SemanticStatus, reason: SemanticReason) -> bool:
    """Whether a terminal attempt outcome may consume another physical attempt slot."""

    if status not in _RETRIABLE_STATUSES or reason not in _RETRIABLE_REASONS:
        return False
    # Quota exhaustion is not a transient 429-class retry; refuse silent multi-dispatch.
    if reason is SemanticReason.PROVIDER_QUOTA_EXHAUSTED:
        return False
    return True


def is_repairable_semantic_outcome(status: SemanticStatus, reason: SemanticReason) -> bool:
    """Whether a terminal attempt outcome is the issue #348 repair class (one retry, ever)."""

    return status is SemanticStatus.INVALID and reason in _REPAIRABLE_REASONS


def repair_retries_from_rows(attempts: tuple[SemanticAttemptRecord, ...]) -> int:
    """Count repair retries already spent on a job from its durable attempt rows.

    A repaired attempt is closed as ``expired`` with the repair-class terminal code, so the count
    is simply the rows carrying such a code. Reading it from rows rather than coordinator memory
    is what keeps the one-repair cap true across a crash or an ``awaiting_human`` replay, where
    the loop restarts with fresh locals but the ledger still remembers the first attempt.
    """

    return sum(1 for attempt in attempts if attempt.terminal_code in _REPAIRABLE_REASONS)


def should_retry_after(
    *,
    status: SemanticStatus,
    reason: SemanticReason,
    attempts_completed: int,
    max_retries: int,
    deadline_expired: bool,
    repair_retries_used: int = 0,
) -> bool:
    """Decide whether another physical attempt is admitted inside the total deadline.

    ``repair_retries_used`` is the number of repair retries the job has already spent (from
    durable rows). A repair-class outcome is admitted only while that count is below the
    one-retry cap; transient classes ignore it. Both classes share the physical-attempt budget.
    """

    if deadline_expired:
        return False
    if type(repair_retries_used) is not int or repair_retries_used < 0:
        raise ValueError("repair_retries_used_invalid")
    if is_retriable_semantic_outcome(status, reason):
        pass
    elif is_repairable_semantic_outcome(status, reason):
        if repair_retries_used >= _REPAIR_RETRY_LIMIT:
            return False
    else:
        return False
    budget = physical_attempt_budget(max_retries)
    return attempts_completed < budget


def attempt_accounting_from_rows(
    job: SemanticJobRecord | None,
    attempts: tuple[SemanticAttemptRecord, ...],
    *,
    max_retries: int,
) -> SemanticAttemptAccounting:
    """Rebuild bounded accounting from durable job/attempt rows."""

    budget = physical_attempt_budget(max_retries)
    counts: dict[str, int] = {}
    for attempt in attempts:
        if attempt.terminal_code is None:
            continue
        token = attempt.terminal_code.value
        counts[token] = counts.get(token, 0) + 1
    ordered = tuple(sorted(counts.items(), key=lambda item: item[0].encode("ascii")))
    attempted = len(attempts)
    selected = None if job is None else job.selected_attempt_id
    exhausted = (
        job is not None
        and job.state in {"failed", "quarantined"}
        and (attempted >= budget or (job.terminal_code is SemanticReason.RETRY_BUDGET_EXHAUSTED))
    )
    return SemanticAttemptAccounting(
        attempted_count=attempted,
        selected_attempt_id=selected,
        exhausted=exhausted,
        terminal_reason_counts=ordered,
    )


def attempt_accounting_to_json(value: SemanticAttemptAccounting) -> dict[str, JsonValue]:
    """Encode accounting as structural JSON with no user-controlled content."""

    return {
        "attempted_count": value.attempted_count,
        "selected_attempt_id": value.selected_attempt_id,
        "exhausted": value.exhausted,
        "terminal_reason_counts": tuple(
            {"reason": reason, "count": count} for reason, count in value.terminal_reason_counts
        ),
    }


def final_status_after_exhaustion(
    last_status: SemanticStatus,
    last_reason: SemanticReason,
    *,
    attempts_completed: int,
    max_retries: int,
) -> tuple[SemanticStatus, SemanticReason]:
    """Map multi-attempt exhaustion to the closed public status/reason pair.

    A single physical attempt that fails with a retriable class keeps its exact reason
    (``max_retries=0``). Exhaustion after using the full budget surfaces
    ``unavailable/retry_budget_exhausted``. A repair-class last outcome is a real provider
    answer, not transport exhaustion: it keeps ``invalid/response_content_invalid`` however many
    attempts preceded it, so a second invalid answer reads as what it was.
    """

    if is_repairable_semantic_outcome(last_status, last_reason):
        return last_status, last_reason
    budget = physical_attempt_budget(max_retries)
    if attempts_completed >= budget and attempts_completed > 1:
        return SemanticStatus.UNAVAILABLE, SemanticReason.RETRY_BUDGET_EXHAUSTED
    if (
        attempts_completed >= budget
        and is_retriable_semantic_outcome(last_status, last_reason)
        and max_retries > 0
    ):
        return SemanticStatus.UNAVAILABLE, SemanticReason.RETRY_BUDGET_EXHAUSTED
    return last_status, last_reason


def status_for_semantic_reason(reason: SemanticReason) -> SemanticStatus:
    """Map a closed SemanticReason to its unique SemanticStatus (no coercion of unknown pairs)."""

    status = _STATUS_BY_REASON.get(reason)
    if status is None:
        raise ValueError("semantic_reason_unmapped")
    return status


BackoffKind = Literal["none", "transient"]


def backoff_seconds(attempt_ordinal: int, *, kind: BackoffKind = "transient") -> float:
    """Deterministic bounded backoff before the next physical attempt (ADR-006 jittered budget)."""

    if kind == "none" or attempt_ordinal <= 1:
        return 0.0
    # Small deterministic backoff: 0.05s, 0.1s — stays well under typical total deadlines.
    base = 0.05 * float(1 << min(attempt_ordinal - 1, 2))
    # Stable pseudo-jitter from ordinal (no clock/random dependency).
    jitter = 0.01 * float((attempt_ordinal * 17) % 5)
    return min(1.0, base + jitter)


class _SemanticAttemptLedger(Protocol):
    async def claim_semantic_job(
        self, lease: OperationLease, job_id: str
    ) -> SemanticAttemptHandle: ...

    async def record_attempt_outcome(
        self,
        handle: SemanticAttemptHandle,
        outcome: AttemptOutcome,
        result_object_ref: ObjectRef | None = None,
        terminal_code: SemanticReason | None = None,
    ) -> None: ...

    async def fail_semantic_job(
        self,
        lease: OperationLease,
        job_id: str,
        terminal_code: SemanticReason,
    ) -> SemanticJobRecord: ...

    async def select_attempt(
        self,
        lease: OperationLease,
        handle: SemanticAttemptHandle,
        selected_result_object_ref: ObjectRef,
    ) -> object: ...

    async def load_semantic_job(
        self, writer_id: str, operation_id: str
    ) -> SemanticJobRecord | None: ...

    async def list_semantic_attempts(self, job_id: str) -> tuple[SemanticAttemptRecord, ...]: ...

    async def record_disclosure_wait(
        self,
        handle: SemanticAttemptHandle,
        pending_id: str,
        pending_expires_at: datetime,
    ) -> object: ...

    async def renew_leases(self, lease: OperationLease) -> OperationLease: ...


class _AttemptEvaluation(Protocol):
    @property
    def status(self) -> SemanticStatus: ...

    @property
    def reason(self) -> SemanticReason: ...

    @property
    def judgment(self) -> object | None: ...

    @property
    def provenance(self) -> object | None: ...


SemanticAttemptDispatch = Callable[
    [SemanticAttemptHandle, Deadline],
    Awaitable[_AttemptEvaluation],
]


async def _accounting_for(
    ledger: _SemanticAttemptLedger,
    lease: OperationLease,
    job_id: str,
    *,
    max_retries: int,
) -> SemanticAttemptAccounting:
    rows = await ledger.list_semantic_attempts(job_id)
    job_final = await ledger.load_semantic_job(lease.writer_id, lease.operation_id)
    return attempt_accounting_from_rows(job_final, rows, max_retries=max_retries)


async def _recover_terminal_job(
    *,
    ledger: _SemanticAttemptLedger,
    lease: OperationLease,
    job: SemanticJobRecord,
    max_retries: int,
    recover_selected: Callable[[SemanticJobRecord], Awaitable[_AttemptEvaluation | None]] | None,
    build_final: Callable[
        [SemanticStatus, SemanticReason, _AttemptEvaluation | None, SemanticAttemptAccounting],
        object,
    ],
) -> object:
    """Rebuild the final evaluation from a durable terminal job without re-claiming."""

    accounting = await _accounting_for(ledger, lease, job.job_id, max_retries=max_retries)
    if job.state == "succeeded":
        evaluation: _AttemptEvaluation | None = None
        if recover_selected is not None:
            evaluation = await recover_selected(job)
        reason = (
            job.terminal_code
            if type(job.terminal_code) is SemanticReason
            else SemanticReason.SEMANTIC_COMPLETED
        )
        return build_final(SemanticStatus.SUCCEEDED, reason, evaluation, accounting)

    reason = (
        job.terminal_code
        if type(job.terminal_code) is SemanticReason
        else SemanticReason.COORDINATOR_FAILURE
    )
    try:
        status = status_for_semantic_reason(reason)
    except ValueError:
        status = SemanticStatus.FAILED
        reason = SemanticReason.COORDINATOR_FAILURE
    return build_final(status, reason, None, accounting)


async def _terminalize_after_failure(
    *,
    ledger: _SemanticAttemptLedger,
    renew: Callable[[], Awaitable[OperationLease]],
    lease_holder: Callable[[], OperationLease],
    job_id: str,
    handle: SemanticAttemptHandle | None,
    max_retries: int,
) -> SemanticAttemptAccounting:
    """Drive a failed attempt and its job to a terminal state, whatever went wrong.

    Total by construction: every step is individually guarded and recorded, because a second
    fault while cleaning up after the first must not re-raise and strand the very state this is
    trying to release. Returns whatever accounting could be read, empty if none.

    ``record_attempt_outcome(FAILED)`` terminalizes the job as well as the attempt, so it is the
    right call whenever an attempt was claimed. ``fail_semantic_job`` is for the other case only —
    it rejects a job that still has an active attempt.
    """

    try:
        await renew()
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="semantic_attempts",
            operation="semantic_terminalize_renew_failed",
        )
    if handle is not None:
        try:
            await ledger.record_attempt_outcome(
                handle,
                AttemptOutcome.FAILED,
                terminal_code=SemanticReason.COORDINATOR_FAILURE,
            )
        except Exception as exc:
            # The attempt may already have left "started" if the raise came after the outcome
            # write; that is the state we wanted, so it is not worth escalating.
            record_unexpected_exception_without_raising(
                exc,
                component="semantic_attempts",
                operation="semantic_terminalize_attempt_failed",
            )
    else:
        try:
            await ledger.fail_semantic_job(
                lease_holder(), job_id, SemanticReason.COORDINATOR_FAILURE
            )
        except Exception as exc:
            record_unexpected_exception_without_raising(
                exc,
                component="semantic_attempts",
                operation="semantic_terminalize_job_failed",
            )
    try:
        return await _accounting_for(ledger, lease_holder(), job_id, max_retries=max_retries)
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="semantic_attempts",
            operation="semantic_terminalize_accounting_failed",
        )
        return attempt_accounting_from_rows(None, (), max_retries=max_retries)


async def _terminalize_cancellation_safe(
    *,
    ledger: _SemanticAttemptLedger,
    renew: Callable[[], Awaitable[OperationLease]],
    lease_holder: Callable[[], OperationLease],
    job_id: str,
    handle: SemanticAttemptHandle | None,
    max_retries: int,
) -> tuple[SemanticAttemptAccounting, bool]:
    """Finish terminal writes even if cancellation is delivered again during cleanup.

    The cleanup runs in its own task so cancelling this coordinator task cannot cancel a ledger
    write through the await chain. ``shield`` still reports each new cancellation to this task;
    remember that fact, keep waiting for cleanup, then let the caller restore cancellation by
    raising after durability and accounting have finished.
    """

    cleanup = asyncio.create_task(
        _terminalize_after_failure(
            ledger=ledger,
            renew=renew,
            lease_holder=lease_holder,
            job_id=job_id,
            handle=handle,
            max_retries=max_retries,
        )
    )
    cancellation_received = False
    while True:
        try:
            accounting = await asyncio.shield(cleanup)
            return accounting, cancellation_received
        except asyncio.CancelledError:
            cancellation_received = True
            if cleanup.done():
                if not cleanup.cancelled():
                    return cleanup.result(), True
                # A cleanup implementation that cancelled itself cannot be resumed. Preserve
                # the signal and return bounded fallback accounting to the caller.
                return (
                    attempt_accounting_from_rows(None, (), max_retries=max_retries),
                    True,
                )


async def run_durable_semantic_attempts(
    *,
    ledger: _SemanticAttemptLedger,
    lease: OperationLease,
    job: SemanticJobRecord,
    deadline: Deadline,
    max_retries: int,
    now_monotonic: Callable[[], float],
    dispatch: SemanticAttemptDispatch,
    publish_success_response: Callable[
        [SemanticAttemptHandle, _AttemptEvaluation], Awaitable[ObjectRef]
    ],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    build_final: Callable[
        [SemanticStatus, SemanticReason, _AttemptEvaluation | None, SemanticAttemptAccounting],
        object,
    ],
    recover_selected: (
        Callable[[SemanticJobRecord], Awaitable[_AttemptEvaluation | None]] | None
    ) = None,
    on_lease_renewed: Callable[[OperationLease], None] | None = None,
) -> object:
    """Run the physical attempt loop for one durable semantic job.

    Each iteration claims (or resumes) one attempt, dispatches once, and records a durable
    outcome. Retries the ADR-006 transient classes, plus at most one issue #348 repair retry
    after ``invalid/response_content_invalid``, within the total deadline and ``max_retries``
    budget. Exactly one selected attempt or one terminal failed job results.

    Crash/replay after a terminal job row already exists recovers from durable state without
    re-claiming. The check operation lease is renewed around each claim/select so a configured
    ``timeout_seconds`` longer than the 60s lease TTL cannot expire mid-operation.
    """

    current_lease = lease

    async def _renew() -> OperationLease:
        nonlocal current_lease
        current_lease = await ledger.renew_leases(current_lease)
        if on_lease_renewed is not None:
            on_lease_renewed(current_lease)
        return current_lease

    # Always refresh the check lease before recovery or the first claim so a long semantic
    # deadline is not truncated by the 60-second operation-lease TTL.
    await _renew()

    # Recover previously terminal jobs (crash after select_attempt / final FAILED before commit).
    if job.state in _TERMINAL_JOB_STATES:
        return await _recover_terminal_job(
            ledger=ledger,
            lease=current_lease,
            job=job,
            max_retries=max_retries,
            recover_selected=recover_selected,
            build_final=build_final,
        )

    budget = physical_attempt_budget(max_retries)
    last: _AttemptEvaluation | None = None
    attempts_completed = 0
    pending_claim_error: PublicOperationError | None = None

    while attempts_completed < budget:
        if deadline.expired(now_monotonic()):
            if last is None and pending_claim_error is not None:
                # Another live owner still has authority over the job. Do not falsify that
                # recoverable state as a coordinator failure or mutate its active attempt.
                raise pending_claim_error
            if last is None:
                await ledger.fail_semantic_job(
                    current_lease,
                    job.job_id,
                    SemanticReason.PROVIDER_TIMEOUT,
                )
                accounting = await _accounting_for(
                    ledger, current_lease, job.job_id, max_retries=max_retries
                )
                return build_final(
                    SemanticStatus.TIMEOUT,
                    SemanticReason.PROVIDER_TIMEOUT,
                    None,
                    accounting,
                )
            status, reason = final_status_after_exhaustion(
                last.status,
                last.reason,
                attempts_completed=attempts_completed,
                max_retries=max_retries,
            )
            await ledger.fail_semantic_job(current_lease, job.job_id, reason)
            accounting = await _accounting_for(
                ledger, current_lease, job.job_id, max_retries=max_retries
            )
            return build_final(status, reason, last, accounting)

        # Keep the operation lease alive across provider latency and backoff.
        await _renew()
        handle: SemanticAttemptHandle | None = None
        try:
            try:
                handle = await ledger.claim_semantic_job(current_lease, job.job_id)
            except PublicOperationError as exc:
                if not exc.retryable:
                    raise
                # A live lease held elsewhere is durable pending state, not a failed physical
                # attempt. Poll inside the total deadline without consuming an attempt slot.
                pending_claim_error = exc
                remaining = deadline.remaining_seconds(now_monotonic())
                if remaining > 0.0:
                    await sleep(min(0.05, remaining))
                continue
            pending_claim_error = None
            remaining = deadline.remaining_seconds(now_monotonic())
            attempt_deadline = Deadline(
                deadline.expires_at_utc,
                now_monotonic() + remaining,
            )
            evaluation = await dispatch(handle, attempt_deadline)
        except BaseException as exc:
            # A raise between claim and the terminal write used to unwind past every
            # terminalizing call, leaving the attempt "started" and the job "leased" forever —
            # and, because claim resumes the same attempt on replay, the operation could never
            # recover. This frame is the only one that knows the durable state, so it finalizes
            # here rather than letting the exception reach the coordinator's catch-all.
            accounting, cancellation_received = await _terminalize_cancellation_safe(
                ledger=ledger,
                renew=_renew,
                lease_holder=lambda: current_lease,
                job_id=job.job_id,
                handle=handle,
                max_retries=max_retries,
            )
            if cancellation_received:
                raise asyncio.CancelledError
            if isinstance(exc, Exception):
                return build_final(
                    SemanticStatus.FAILED,
                    SemanticReason.COORDINATOR_FAILURE,
                    None,
                    accounting,
                )
            # Cancellation and other BaseExceptions still propagate — but only after the durable
            # state has been made terminal.
            raise
        attempts_completed = handle.attempt_ordinal
        last = evaluation

        # Provider dispatch may consume most of a lease TTL; renew before ledger mutations.
        await _renew()

        if evaluation.status is SemanticStatus.SUCCEEDED:
            response_ref = await publish_success_response(handle, evaluation)
            await ledger.record_attempt_outcome(
                handle, AttemptOutcome.RESPONSE_DURABLE, response_ref
            )
            await ledger.select_attempt(current_lease, handle, response_ref)
            accounting = await _accounting_for(
                ledger, current_lease, job.job_id, max_retries=max_retries
            )
            return build_final(evaluation.status, evaluation.reason, evaluation, accounting)

        # awaiting_human is the one nonterminal dispatch outcome. It is not a failure and not a
        # retry: no provider was reached, the case was never sent, and the same attempt must be
        # resumable once the human answers. Falling through to the exhaustion path below would
        # write the attempt FAILED, terminalize the job, and commit a terminal check result —
        # leaving an approved decision with nothing left to resume.
        if evaluation.status is SemanticStatus.AWAITING_HUMAN:
            continuation = getattr(evaluation, "continuation", None)
            if continuation is None:
                # Without a pending id the caller cannot act and the job cannot be resumed;
                # that is a coordinator failure, not a wait.
                accounting = await _terminalize_after_failure(
                    ledger=ledger,
                    renew=_renew,
                    lease_holder=lambda: current_lease,
                    job_id=job.job_id,
                    handle=handle,
                    max_retries=max_retries,
                )
                return build_final(
                    SemanticStatus.FAILED,
                    SemanticReason.COORDINATOR_FAILURE,
                    None,
                    accounting,
                )
            await ledger.record_disclosure_wait(
                handle,
                continuation.pending_id,
                continuation.expires_at.as_datetime(),
            )
            accounting = await _accounting_for(
                ledger, current_lease, job.job_id, max_retries=max_retries
            )
            # The attempt stays `started` and the job stays `leased` on purpose: this is the
            # durable record that the check is open, not finished.
            return build_final(evaluation.status, evaluation.reason, evaluation, accounting)

        # The repair cap is read from durable rows, not a local counter: after an
        # ``awaiting_human`` replay this loop starts over with ``attempts_completed`` rebuilt
        # from the claimed ordinal, and the one-repair rule has to survive that the same way.
        repair_retries_used = repair_retries_from_rows(
            await ledger.list_semantic_attempts(job.job_id)
        )
        can_retry = should_retry_after(
            status=evaluation.status,
            reason=evaluation.reason,
            attempts_completed=attempts_completed,
            max_retries=max_retries,
            deadline_expired=deadline.expired(now_monotonic()),
            repair_retries_used=repair_retries_used,
        )
        if can_retry:
            # ``expired`` keeps the job claimable and leaves this attempt's terminal code in the
            # row, so the repaired attempt stays visible in accounting next to its successor.
            await ledger.record_attempt_outcome(
                handle,
                AttemptOutcome.EXPIRED,
                terminal_code=evaluation.reason,
            )
            # A repair is not waiting out a transport fault; resubmit without backoff.
            repair = is_repairable_semantic_outcome(evaluation.status, evaluation.reason)
            delay = backoff_seconds(handle.attempt_ordinal, kind="none" if repair else "transient")
            if delay > 0.0 and not deadline.expired(now_monotonic() + delay):
                await sleep(delay)
            continue

        terminal_status, terminal_reason = final_status_after_exhaustion(
            evaluation.status,
            evaluation.reason,
            attempts_completed=attempts_completed,
            max_retries=max_retries,
        )
        await ledger.record_attempt_outcome(
            handle,
            AttemptOutcome.FAILED,
            terminal_code=terminal_reason,
        )
        accounting = await _accounting_for(
            ledger, current_lease, job.job_id, max_retries=max_retries
        )
        return build_final(terminal_status, terminal_reason, evaluation, accounting)

    if last is None:
        await ledger.fail_semantic_job(
            current_lease,
            job.job_id,
            SemanticReason.RETRY_BUDGET_EXHAUSTED,
        )
        accounting = await _accounting_for(
            ledger, current_lease, job.job_id, max_retries=max_retries
        )
        return build_final(
            SemanticStatus.UNAVAILABLE,
            SemanticReason.RETRY_BUDGET_EXHAUSTED,
            None,
            accounting,
        )
    status, reason = final_status_after_exhaustion(
        last.status,
        last.reason,
        attempts_completed=attempts_completed,
        max_retries=max_retries,
    )
    await ledger.fail_semantic_job(current_lease, job.job_id, reason)
    accounting = await _accounting_for(ledger, current_lease, job.job_id, max_retries=max_retries)
    return build_final(status, reason, last, accounting)
