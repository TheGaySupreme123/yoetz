"""Bounded asynchronous semantic review for observation advice (issue #619).

Hook ingest never calls a provider. The advice builder asks the scheduler for the durable
attempt that matches the current evidence basis; when none exists it enqueues one minimized
packet and reports ``advice_semantic_pending``. A generation-fenced background worker later
claims the row, resolves repository-scoped provider authority at dispatch time, performs the
privacy-gated attempt, and records the outcome. Only a ``succeeded`` row with validated output
may add semantic advice; every other state stays a truthful bounded coverage gap.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

from yoetz.application.observation_advice import (
    ObservationAdviceCandidate,
    ObservationAdviceSemanticAddon,
    minimized_semantic_evidence_packet,
)
from yoetz.domain.findings import FindingId
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode

__all__ = [
    "ADVICE_SEMANTIC_FAILURE_REASONS",
    "ADVICE_SEMANTIC_PENDING_REASON",
    "DEFAULT_ADVICE_SEMANTIC_MAX_PENDING",
    "MAX_ADVICE_SEMANTIC_ATTEMPTS",
    "AdviceSemanticDispatch",
    "AdviceSemanticDrainHandle",
    "ObservationAdviceSemanticAttempt",
    "ObservationAdviceSemanticOutcome",
    "ObservationAdviceSemanticRepository",
    "ObservationAdviceSemanticScheduler",
    "ObservationAdviceSemanticSupervisor",
    "ObservationAdviceSemanticWorker",
    "addon_from_attempt",
]

type AttemptStatus = Literal[
    "pending", "running", "succeeded", "failed", "unavailable", "cancelled"
]
type TerminalStatus = Literal["succeeded", "failed", "unavailable", "cancelled"]

# Closed vocabulary shared with the migration CHECK constraint. ``pending`` is not a stored
# failure reason: it is the addon marker for a row that has not reached a terminal state.
ADVICE_SEMANTIC_PENDING_REASON: Final = "pending"
ADVICE_SEMANTIC_FAILURE_REASONS: Final[frozenset[str]] = frozenset(
    {
        "queue_full",
        "superseded",
        "authorization_missing",
        "provider_unavailable",
        "provider_failed",
        "output_invalid",
        "cancelled",
        "interrupted",
    }
)
# At most this many pending rows per task bundle; the next schedule records ``queue_full``
# without a provider attempt so the bound is visible instead of silent.
DEFAULT_ADVICE_SEMANTIC_MAX_PENDING: Final = 16
# A row reclaimed this many times without a terminal outcome is recorded ``interrupted``.
MAX_ADVICE_SEMANTIC_ATTEMPTS: Final = 3


@dataclass(frozen=True, slots=True)
class ObservationAdviceSemanticAttempt:
    """One durable attempt row keyed by (workspace, session, evidence basis)."""

    attempt_id: str
    workspace_commitment: str
    yoetz_session_id: str
    basis_digest: str
    subject_digest: str
    coverage_gaps: tuple[str, ...]
    packet_json: bytes
    status: AttemptStatus
    state_token: int
    attempt_count: int = 0
    failure_reason: str | None = None
    attempt_receipt: str | None = None
    provider_identity: str | None = None
    finding_ids: tuple[str, ...] = ()
    evidence_digest: str | None = None
    summaries: tuple[str, ...] = ()
    details: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObservationAdviceSemanticOutcome:
    """Terminal result of one dispatch, as recorded on the durable row."""

    status: TerminalStatus
    failure_reason: str | None = None
    attempt_receipt: str | None = None
    provider_identity: str | None = None
    finding_ids: tuple[str, ...] = ()
    evidence_digest: str | None = None
    summaries: tuple[str, ...] = ()
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "succeeded":
            if self.failure_reason is not None:
                raise ValueError("advice_semantic_outcome_invalid")
        elif self.failure_reason not in ADVICE_SEMANTIC_FAILURE_REASONS:
            raise ValueError("advice_semantic_outcome_invalid")
        if self.status != "succeeded" and self.finding_ids:
            raise ValueError("advice_semantic_outcome_invalid")


class ObservationAdviceSemanticRepository(Protocol):
    """Generation-fenced durable attempt repository (one per task bundle)."""

    def schedule(
        self,
        *,
        workspace: str,
        yoetz_session_id: str,
        basis_digest: str,
        subject_digest: str,
        coverage_gaps: tuple[str, ...],
        packet_json: bytes,
        enqueued_at: str,
        max_pending: int,
    ) -> ObservationAdviceSemanticAttempt: ...

    def lookup(
        self, *, yoetz_session_id: str, basis_digest: str
    ) -> ObservationAdviceSemanticAttempt | None: ...

    def claim_next(
        self,
        *,
        service_generation: int,
        lease_owner: str,
        lease_expires_at: str,
        now: str,
    ) -> ObservationAdviceSemanticAttempt | None: ...

    def complete(
        self,
        *,
        attempt: ObservationAdviceSemanticAttempt,
        service_generation: int,
        lease_owner: str,
        outcome: ObservationAdviceSemanticOutcome,
        recorded_at: str,
    ) -> None: ...

    def list_pending_workspaces(self) -> tuple[str, ...]: ...


type AdviceSemanticDispatch = Callable[
    [ObservationAdviceSemanticAttempt], Awaitable[ObservationAdviceSemanticOutcome]
]
type NowProvider = Callable[[], str]


def addon_from_attempt(
    attempt: ObservationAdviceSemanticAttempt | None,
) -> ObservationAdviceSemanticAddon | None:
    """Project a durable row onto the additive advice addon without inventing success."""

    if attempt is None:
        return None
    if attempt.status == "succeeded":
        return ObservationAdviceSemanticAddon(
            finding_ids=cast(tuple[FindingId, ...], attempt.finding_ids),
            evidence_digest=attempt.evidence_digest,
            next_action=None,
            summaries=attempt.summaries,
            details=attempt.details,
            provider_identity=attempt.provider_identity,
            attempt_receipt=attempt.attempt_receipt,
            failure_reason=None,
        )
    if attempt.status in {"pending", "running"}:
        return ObservationAdviceSemanticAddon(
            finding_ids=(),
            evidence_digest=None,
            next_action=None,
            summaries=(),
            details=(),
            provider_identity=None,
            attempt_receipt=None,
            failure_reason=ADVICE_SEMANTIC_PENDING_REASON,
        )
    reason = (
        attempt.failure_reason
        if attempt.failure_reason in ADVICE_SEMANTIC_FAILURE_REASONS
        else "provider_failed"
    )
    return ObservationAdviceSemanticAddon(
        finding_ids=(),
        evidence_digest=None,
        next_action=None,
        summaries=(),
        details=(),
        provider_identity=attempt.provider_identity,
        attempt_receipt=attempt.attempt_receipt,
        failure_reason=reason,
    )


@dataclass(frozen=True, slots=True)
class ObservationAdviceSemanticScheduler:
    """Advice-build side of the worker: look up or enqueue, never dispatch.

    ``review`` runs inside the advice build on the hook path, so it performs only local
    repository reads and one bounded insert. The scoped observation gaps are stored with the
    row and reach the provider packet byte-for-byte; the old inline callback dropped them.
    """

    now: NowProvider
    max_pending: int = DEFAULT_ADVICE_SEMANTIC_MAX_PENDING
    enabled: bool = True

    async def review(
        self,
        *,
        store: object,
        workspace: str,
        candidates: Sequence[ObservationAdviceCandidate],
        basis: str,
        gaps: Sequence[str],
        yoetz_session_id: str | None,
    ) -> ObservationAdviceSemanticAddon | None:
        if not self.enabled or yoetz_session_id is None or not candidates:
            return None
        repository = _repository_for(store)
        if repository is None:
            return None
        existing = repository.lookup(yoetz_session_id=yoetz_session_id, basis_digest=basis)
        if existing is not None:
            return addon_from_attempt(existing)
        sorted_gaps = tuple(sorted({gap for gap in gaps if gap}, key=str.encode))
        packet = minimized_semantic_evidence_packet(
            candidates,
            basis,
            coverage_gaps=sorted_gaps,
            finding_summaries=tuple(str(item.rule_code) for item in candidates),
        )
        payload = canonical_encode(cast(JsonValue, dict(packet)))
        subject = (
            basis
            if basis.startswith("sha256:")
            else canonical_digest(cast(JsonValue, {"basis": basis}))
        )
        scheduled = repository.schedule(
            workspace=workspace,
            yoetz_session_id=yoetz_session_id,
            basis_digest=basis,
            subject_digest=subject,
            coverage_gaps=sorted_gaps,
            packet_json=payload,
            enqueued_at=self.now(),
            max_pending=self.max_pending,
        )
        return addon_from_attempt(scheduled)


def _repository_for(store: object) -> ObservationAdviceSemanticRepository | None:
    factory = getattr(store, "advice_semantic_repository", None)
    if not callable(factory):
        return None
    repository = factory()
    if repository is None:
        return None
    return cast(ObservationAdviceSemanticRepository, repository)


@dataclass
class ObservationAdviceSemanticWorker:
    """Run one serialized, generation-fenced durable semantic attempt at a time."""

    repository: ObservationAdviceSemanticRepository
    dispatch: AdviceSemanticDispatch
    service_generation: int
    lease_owner: str
    now: NowProvider
    lease_expires_at: NowProvider

    async def run_once(self) -> ObservationAdviceSemanticAttempt | None:
        attempt = self.repository.claim_next(
            service_generation=self.service_generation,
            lease_owner=self.lease_owner,
            lease_expires_at=self.lease_expires_at(),
            now=self.now(),
        )
        if attempt is None:
            return None
        try:
            outcome = await self.dispatch(attempt)
        except asyncio.CancelledError:
            self._complete(
                attempt,
                ObservationAdviceSemanticOutcome(status="cancelled", failure_reason="cancelled"),
            )
            raise
        except Exception:
            outcome = ObservationAdviceSemanticOutcome(
                status="failed", failure_reason="provider_failed"
            )
        self._complete(attempt, outcome)
        return attempt

    def _complete(
        self,
        attempt: ObservationAdviceSemanticAttempt,
        outcome: ObservationAdviceSemanticOutcome,
    ) -> None:
        with contextlib.suppress(Exception):
            self.repository.complete(
                attempt=attempt,
                service_generation=self.service_generation,
                lease_owner=self.lease_owner,
                outcome=outcome,
                recorded_at=self.now(),
            )


@dataclass(frozen=True, slots=True)
class AdviceSemanticDrainHandle:
    """One workspace's durable semantic worker plus optional post-attempt hook."""

    workspace_commitment: str
    worker: ObservationAdviceSemanticWorker
    after_complete: Callable[[], Awaitable[None]] | None = None
    on_idle: Callable[[], Awaitable[None]] | None = None


@dataclass
class ObservationAdviceSemanticSupervisor:
    """Generation-fenced background semantic advice owned by the ready lifecycle.

    Mirrors ``ObservationVerificationSupervisor``: the hook path only enqueues and wakes it;
    provider attempts never execute inside a hook RPC budget. Restart recovery registers
    handles for workspaces that still hold pending rows, and ``claim_next`` reclaims leases
    left by a previous service generation.
    """

    service_generation: int

    def __post_init__(self) -> None:
        self._handles: dict[str, AdviceSemanticDrainHandle] = {}
        self._wake = asyncio.Event()
        self._closed = False
        self._loop_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def register(self, handle: AdviceSemanticDrainHandle) -> bool:
        if self._closed:
            return False
        if handle.workspace_commitment in self._handles:
            self._wake.set()
            return False
        self._handles[handle.workspace_commitment] = handle
        self._wake.set()
        return True

    def has_handle(self, workspace_commitment: str) -> bool:
        return workspace_commitment in self._handles

    @property
    def closed(self) -> bool:
        return self._closed

    def unregister(self, workspace_commitment: str) -> None:
        self._handles.pop(workspace_commitment, None)

    def notify(self, workspace_commitment: str | None = None) -> None:
        del workspace_commitment
        if not self._closed:
            self._wake.set()

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        self._closed = False
        self._wake.set()
        self._loop_task = asyncio.create_task(self._run_loop(), name="observation-advice-semantic")

    async def rediscover(
        self,
        builders: Mapping[str, Callable[[], AdviceSemanticDrainHandle | None]],
    ) -> None:
        for workspace, builder in sorted(builders.items(), key=lambda item: item[0].encode()):
            if self._closed:
                return
            if workspace in self._handles:
                continue
            handle = builder()
            if handle is None:
                continue
            self.register(handle)
        self.notify()

    async def stop(self) -> None:
        self._closed = True
        self._wake.set()
        task = self._loop_task
        self._loop_task = None
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        handles = tuple(self._handles.values())
        self._handles.clear()
        for handle in handles:
            if handle.on_idle is not None:
                await handle.on_idle()

    async def drain_once(self) -> None:
        """Run every registered worker to idle once; tests drive this without the loop."""

        await self._drain_once()

    async def _run_loop(self) -> None:
        while not self._closed:
            self._wake.clear()
            await self._drain_once()
            if self._closed:
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=2.0)
            except TimeoutError:
                pass

    async def _drain_once(self) -> None:
        async with self._lock:
            handles = tuple(self._handles.values())
        for handle in handles:
            if self._closed:
                return
            if handle.worker.service_generation != self.service_generation:
                continue
            while not self._closed:
                attempt = await handle.worker.run_once()
                if attempt is None:
                    if self._handles.get(handle.workspace_commitment) is handle:
                        self.unregister(handle.workspace_commitment)
                        if handle.on_idle is not None:
                            await handle.on_idle()
                    break
                if handle.after_complete is not None:
                    await handle.after_complete()
