"""Durable semantic-operation attempt budget, retry matrix, and accounting.

ADR-006: one durable semantic operation, at most two retries (``max_retries``), one total
deadline (``timeout_seconds``), and one physical attempt identity per dispatch. The ledger's
``semantic_jobs`` / ``semantic_attempts`` tables are the recovery authority — never a
memory-only coordinator object.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol

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
from yoetz.protocol.models import VALID_SEMANTIC_REASONS, SemanticReason, SemanticStatus

__all__ = [
    "SemanticAttemptAccounting",
    "SemanticAttemptDispatch",
    "attempt_accounting_from_rows",
    "attempt_accounting_to_json",
    "backoff_seconds",
    "final_status_after_exhaustion",
    "is_retriable_semantic_outcome",
    "max_physical_attempts",
    "physical_attempt_budget",
    "run_durable_semantic_attempts",
    "should_retry_after",
    "status_for_semantic_reason",
]

# ADR-006 approved transient classes only. Contract-invalid is not automatically retried.
_RETRIABLE_REASONS: Final[frozenset[SemanticReason]] = frozenset(
    {
        SemanticReason.PROVIDER_TIMEOUT,
        SemanticReason.TRANSPORT_UNAVAILABLE,
        SemanticReason.PROVIDER_RATE_LIMITED,
    }
)

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


def should_retry_after(
    *,
    status: SemanticStatus,
    reason: SemanticReason,
    attempts_completed: int,
    max_retries: int,
    deadline_expired: bool,
) -> bool:
    """Decide whether another physical attempt is admitted inside the total deadline."""

    if deadline_expired:
        return False
    if not is_retriable_semantic_outcome(status, reason):
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
    ``unavailable/retry_budget_exhausted``.
    """

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
    outcome. Retries only the ADR-006 transient classes within the total deadline and
    ``max_retries`` budget. Exactly one selected attempt or one terminal failed job results.

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

    while attempts_completed < budget:
        if deadline.expired(now_monotonic()):
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
        handle = await ledger.claim_semantic_job(current_lease, job.job_id)
        remaining = deadline.remaining_seconds(now_monotonic())
        attempt_deadline = Deadline(
            deadline.expires_at_utc,
            now_monotonic() + remaining,
        )
        evaluation = await dispatch(handle, attempt_deadline)
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

        can_retry = should_retry_after(
            status=evaluation.status,
            reason=evaluation.reason,
            attempts_completed=attempts_completed,
            max_retries=max_retries,
            deadline_expired=deadline.expired(now_monotonic()),
        )
        if can_retry:
            await ledger.record_attempt_outcome(
                handle,
                AttemptOutcome.EXPIRED,
                terminal_code=evaluation.reason,
            )
            delay = backoff_seconds(handle.attempt_ordinal)
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
    accounting = await _accounting_for(
        ledger, current_lease, job.job_id, max_retries=max_retries
    )
    return build_final(status, reason, last, accounting)
