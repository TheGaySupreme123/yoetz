"""Compose workspace inspection and approved checks bound to subject-state digests."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

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
    "InspectionOrchestrationResult",
    "SubjectStateDigestFn",
    "orchestrate_changed_path_inspection",
    "run_bound_approved_check",
]

type SubjectStateDigestFn = Callable[[LocalWorkspaceHandle], str]


@dataclass(frozen=True, slots=True)
class InspectionOrchestrationResult:
    inspect: WorkspaceInspectResult | None
    inspect_fact: ObservationInspectFact | None
    relative_paths: tuple[str, ...]


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
