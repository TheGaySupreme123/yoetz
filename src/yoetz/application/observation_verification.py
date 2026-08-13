"""Compose workspace inspection and approved checks bound to subject-state digests."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from yoetz.adapters.approved_checks import (
    ApprovedCheckApproval,
    ApprovedCheckCommand,
    ApprovedCheckResult,
    ApprovedCheckRunner,
    ApprovedCheckStatus,
)
from yoetz.kernel.policies.observation_advice import ObservationCheckFact, ObservationInspectFact
from yoetz.ports.subject_state import LocalWorkspaceHandle
from yoetz.ports.workspace_inspect import (
    WorkspaceInspectCommand,
    WorkspaceInspectPort,
    WorkspaceInspectResult,
    WorkspaceInspectStatus,
)

__all__ = [
    "CompletedApprovedCheck",
    "InspectionOrchestrationResult",
    "ObservationVerificationJob",
    "ObservationVerificationRepository",
    "ObservationVerificationSupervisor",
    "ObservationVerificationWorker",
    "SubjectStateDigestFn",
    "VerificationDrainHandle",
    "orchestrate_changed_path_inspection",
    "run_bound_approved_check",
]

type SubjectStateDigestFn = Callable[[LocalWorkspaceHandle], str]
type ApprovedCheckMaterializer = Callable[["CompletedApprovedCheck"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class InspectionOrchestrationResult:
    inspect: WorkspaceInspectResult | None
    inspect_fact: ObservationInspectFact | None
    relative_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ObservationVerificationJob:
    job_id: str
    workspace_commitment: str
    policy_digest: str
    approval_commitment: str
    subject_state_digest: str
    state_token: int


@dataclass(frozen=True, slots=True)
class CompletedApprovedCheck:
    job: ObservationVerificationJob
    approval_id: str
    result: ApprovedCheckResult
    subject_state_after: str | None
    output_object_id: str | None
    is_current: bool
    recorded_at: str


class ObservationVerificationRepository(Protocol):
    """Generation-fenced durable verification job/result repository."""

    def enqueue_latest(
        self,
        *,
        workspace: str,
        policy_digest: str,
        approvals: tuple[str, ...],
        subject_state_digest: str,
        enqueued_at: str,
    ) -> tuple[str, ...]: ...

    def claim_next(
        self,
        *,
        service_generation: int,
        lease_owner: str,
        lease_expires_at: str,
        now: str,
    ) -> ObservationVerificationJob | None: ...

    def list_pending_workspaces(self) -> tuple[str, ...]: ...

    def complete(
        self,
        *,
        job: ObservationVerificationJob,
        service_generation: int,
        lease_owner: str,
        check_id: str,
        result: ApprovedCheckResult,
        subject_state_after: str | None,
        result_commitment: str,
        output_object_id: str | None,
        limitations_json: bytes,
        is_current: bool,
        recorded_at: str,
    ) -> None: ...


WorkspaceProvider = Callable[[str], LocalWorkspaceHandle]
PolicyProvider = Callable[[str, str], tuple[ApprovedCheckApproval, ...]]
SubjectDigestProvider = Callable[[LocalWorkspaceHandle], str]
OutputPersister = Callable[[ObservationVerificationJob, bytes], Awaitable[str | None]]
NowProvider = Callable[[], str]


@dataclass(frozen=True, slots=True)
class VerificationDrainHandle:
    """One workspace's durable verification worker plus optional post-drain hook."""

    workspace_commitment: str
    worker: ObservationVerificationWorker
    after_complete: Callable[[], Awaitable[None]] | None = None
    on_idle: Callable[[], Awaitable[None]] | None = None


@dataclass
class ObservationVerificationSupervisor:
    """Generation-fenced background verification owned by the ready lifecycle.

    Hook ingest only enqueues durable jobs and wakes this supervisor. Approved
    checks never execute inside the hook RPC budget. Startup discovers already-
    pending work via registered drain handles; expired leases are reclaimed by
    ``claim_next``. Shutdown waits for the loop to stop before vault/runtime close.
    """

    service_generation: int

    def __post_init__(self) -> None:
        self._handles: dict[str, VerificationDrainHandle] = {}
        self._wake = asyncio.Event()
        self._closed = False
        self._loop_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def register(self, handle: VerificationDrainHandle) -> bool:
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
        self._loop_task = asyncio.create_task(self._run_loop(), name="observation-verification")

    async def rediscover(
        self,
        builders: Mapping[str, Callable[[], VerificationDrainHandle | None]],
    ) -> None:
        """Register drain handles for workspaces that still have durable pending jobs.

        ``builders`` maps workspace commitment → factory that rebuilds a worker/handle
        (or returns None when the workspace cannot be opened yet). Called once after
        ready-lifecycle start so restart reclaim can complete.
        """

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

    async def _run_loop(self) -> None:
        while not self._closed:
            # Consume the wake that started this pass before draining. A handle
            # registered by an on-idle handoff during the drain then leaves the
            # event set and starts the successor pass immediately.
            self._wake.clear()
            await self._drain_once()
            if self._closed:
                break
            # Always park on the wake event so an empty handle set cannot busy-loop
            # and starve Application.close / supervisor.stop.
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
                job = await handle.worker.run_once()
                if job is None:
                    if self._handles.get(handle.workspace_commitment) is handle:
                        self.unregister(handle.workspace_commitment)
                        if handle.on_idle is not None:
                            await handle.on_idle()
                    break
                if handle.after_complete is not None:
                    await handle.after_complete()


@dataclass
class ObservationVerificationWorker:
    """Run one serialized, generation-fenced durable verification lease at a time."""

    repository: ObservationVerificationRepository
    runner: ApprovedCheckRunner
    workspace_provider: WorkspaceProvider
    policy_provider: PolicyProvider
    capture_subject_state: SubjectDigestProvider
    persist_output: OutputPersister
    service_generation: int
    lease_owner: str
    now: NowProvider
    lease_expires_at: NowProvider
    materialize_result: ApprovedCheckMaterializer | None = None

    def enqueue_if_changed(
        self,
        *,
        workspace: str,
        policy_digest: str,
        approvals: tuple[ApprovedCheckApproval, ...],
        previous_subject_state_digest: str | None,
        subject_state_digest: str,
    ) -> tuple[str, ...]:
        if previous_subject_state_digest == subject_state_digest:
            return ()
        return self.repository.enqueue_latest(
            workspace=workspace,
            policy_digest=policy_digest,
            approvals=tuple(item.approval_commitment for item in approvals),
            subject_state_digest=subject_state_digest,
            enqueued_at=self.now(),
        )

    async def run_once(self) -> ObservationVerificationJob | None:
        job = self.repository.claim_next(
            service_generation=self.service_generation,
            lease_owner=self.lease_owner,
            lease_expires_at=self.lease_expires_at(),
            now=self.now(),
        )
        if job is None:
            return None
        workspace = self.workspace_provider(job.workspace_commitment)
        approvals = self.policy_provider(job.workspace_commitment, job.policy_digest)
        approval = next(
            (item for item in approvals if item.approval_commitment == job.approval_commitment),
            None,
        )
        if approval is None:
            raise RuntimeError("verification_policy_authority_missing")
        captured: list[bytes] = []
        original_sink = getattr(self.runner, "_output_sink", None)
        setattr(self.runner, "_output_sink", captured.append)
        try:
            result, _fact = await asyncio.to_thread(
                run_bound_approved_check,
                runner=self.runner,
                workspace=workspace,
                approval=approval,
                expected_subject_state_digest=job.subject_state_digest,
                capture_subject_state=self.capture_subject_state,
                cursor_event_position=job.state_token,
            )
        finally:
            setattr(self.runner, "_output_sink", original_sink)
        after = self.capture_subject_state(workspace)
        current = after == job.subject_state_digest
        output_object_id = (
            await self.persist_output(job, captured[-1]) if captured and captured[-1] else None
        )
        recorded_at = self.now()
        if self.materialize_result is not None:
            await self.materialize_result(
                CompletedApprovedCheck(
                    job=job,
                    approval_id=approval.approval_id,
                    result=result,
                    subject_state_after=after,
                    output_object_id=output_object_id,
                    is_current=current,
                    recorded_at=recorded_at,
                )
            )
        self.repository.complete(
            job=job,
            service_generation=self.service_generation,
            lease_owner=self.lease_owner,
            check_id=approval.approval_id,
            result=result,
            subject_state_after=after,
            result_commitment=result.result_digest,
            output_object_id=output_object_id,
            limitations_json=b"[]",
            is_current=current,
            recorded_at=recorded_at,
        )
        return job


def orchestrate_changed_path_inspection(
    *,
    workspace: LocalWorkspaceHandle,
    inspect_port: WorkspaceInspectPort,
    relative_paths: Sequence[str],
    changed_paths_digest: str | None = None,
) -> InspectionOrchestrationResult:
    """Inspect only consented, explicitly selected project-relative paths."""

    paths = tuple(relative_paths)
    if not paths:
        return InspectionOrchestrationResult(None, None, ())
    result = inspect_port.inspect(
        WorkspaceInspectCommand(workspace=workspace, relative_paths=paths)
    )
    if result.status is WorkspaceInspectStatus.REJECTED:
        return InspectionOrchestrationResult(result, None, paths)
    selection = result.selection_digest or "sha256:" + ("0" * 64)
    fact = ObservationInspectFact(
        selection_digest=selection,
        relative_paths=paths,
        changed_paths_digest=changed_paths_digest,
    )
    return InspectionOrchestrationResult(result, fact, paths)


def run_bound_approved_check(
    *,
    runner: ApprovedCheckRunner,
    workspace: LocalWorkspaceHandle,
    approval: ApprovedCheckApproval,
    expected_subject_state_digest: str,
    capture_subject_state: SubjectStateDigestFn,
    cursor_event_position: int,
) -> tuple[ApprovedCheckResult, ObservationCheckFact | None]:
    """Recompute subject state before/after; pre-mismatch is STALE without executing."""

    pre = capture_subject_state(workspace)
    result = runner.run(
        ApprovedCheckCommand(
            workspace=workspace,
            approval=approval,
            subject_state_digest=pre,
            expected_subject_state_digest=expected_subject_state_digest,
        )
    )
    if result.status is ApprovedCheckStatus.STALE:
        return result, None
    post = capture_subject_state(workspace)
    if result.status is ApprovedCheckStatus.PASSED and post != pre:
        # Record success but do not treat as current verification after state drift.
        fact = ObservationCheckFact(
            approval_commitment=approval.approval_commitment,
            subject_state_digest=pre,
            status="passed_not_current",
            cursor_event_position=cursor_event_position,
            is_current=False,
        )
        return result, fact
    if result.status is ApprovedCheckStatus.PASSED and post == pre:
        fact = ObservationCheckFact(
            approval_commitment=approval.approval_commitment,
            subject_state_digest=post,
            status="passed",
            cursor_event_position=cursor_event_position,
            is_current=True,
        )
        return result, fact
    fact = ObservationCheckFact(
        approval_commitment=approval.approval_commitment,
        subject_state_digest=post,
        status=result.status.value,
        cursor_event_position=cursor_event_position,
        is_current=False,
    )
    return result, fact


def check_facts_from_results(
    facts: Mapping[str, ObservationCheckFact],
) -> tuple[ObservationCheckFact, ...]:
    return tuple(facts[key] for key in sorted(facts, key=str.encode))
