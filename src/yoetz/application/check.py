"""Deterministic check coordination and semantic-result validation fences."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal, Protocol, cast

from yoetz.domain.coordination import CoordinationError, CoordinationErrorCode
from yoetz.domain.events import LedgerRecord
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CandidateFinding,
    Finding,
    FindingKind,
    FindingOrigin,
    RankedFindings,
    SemanticProvenance,
    finding_from_json,
    finding_to_json,
    semantic_provenance_to_json,
)
from yoetz.domain.receipts import (
    COMPLETION_SCOPE_DECLARED_NONE_GAP,
    COMPLETION_SCOPE_UNDECLARED_GAP,
    OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
    OPTIONAL_SEMANTIC_REVIEW_REGISTRATION_DRIFT_GAP,
    SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP,
    SEMANTIC_CHALLENGES_REJECTED_GAP,
    SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
    SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP,
    SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    semantic_coverage_gap_code,
)
from yoetz.domain.values import (
    REPOSITORY_GRANT_CONTINUATION_KIND,
    ClaimId,
    EventId,
    FindingId,
    Frontier,
    ObligationId,
    SemanticContinuation,
    claim_id,
    event_id,
    finding_id,
    freeze_json,
    obligation_id,
)
from yoetz.kernel.deterministic_checks import (
    DETERMINISTIC_TEXT_CONTRACT_DIGEST,
    DeterministicAssessment,
    DeterministicCase,
    FindingBasisRef,
    case_coverage,
    finding_basis_from_json,
    finding_basis_to_json,
    render_deterministic_finding_text,
)
from yoetz.kernel.lineage import LineageEvaluation, evaluate_recorded_lineage
from yoetz.kernel.policies.research_evidence import research_evidence_findings
from yoetz.kernel.policies.response_support import (
    RESEARCH_REJECTION_PRESENT_FACT,
    WORK_RESPONSE_PRESENT_FACT,
)
from yoetz.kernel.policies.work_integrity import work_integrity_findings
from yoetz.kernel.projections import PROJECTION_VERSION, ProjectionState
from yoetz.kernel.ranking import CheckCompleteness, RankingContext, rank_findings
from yoetz.kernel.reducers import replay
from yoetz.observability.logging import (
    record_bounded_counts_without_raising,
    record_unexpected_exception_without_raising,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.control import McpHostProfile
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    CheckAdvisoryNote,
    CheckAwaitingHuman,
    CheckChildPreviewItem,
    CheckChildrenPreview,
    CheckCommitResult,
    CheckPhase,
    CheckPolicyExecution,
    CheckVersionSlice,
    FrozenCase,
    OperationLease,
    OperationRecord,
    OperationState,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.runtime import BundleRuntimePort, RouteAccess, RouteCommand, TaskRuntime
from yoetz.ports.semantic import ReviewerChallenge, SemanticJudgment
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.coverage import (
    Coverage,
    LedgerFreshness,
    coverage_to_json,
    weakest,
)
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import (
    CheckRequest,
    CheckScopeModel,
    SemanticReason,
    SemanticStatus,
    validate_semantic_outcome,
    validate_semantic_provenance_binding,
)
from yoetz.version import ENGINE_VERSION

__all__ = [
    "SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM",
    "SEMANTIC_REJECTED_REF_OUTSIDE_CASE",
    "Application",
    "CheckScope",
    "FinalSemanticEvaluation",
    "SemanticJudgmentRejected",
    "SemanticJudgmentReview",
    "allocate_findings",
    "carried_semantic_attempt_gaps",
    "case_coverage",
    "check_awaiting_human_json",
    "check_internal_json",
    "execute_check",
    "execute_check_commit",
    "normalize_check_scope",
    "prior_finding_ids",
    "run_deterministic_policies",
    "semantic_coverage_gap_code",
    "validate_semantic_judgment",
]

_RESEARCH_PACK = "research-evidence/0.1.0"
_WORK_PACK = "work-integrity/0.1.0"
_COORDINATION_PACK = "coordination/0.1.0"
_CANONICAL_PACKS = (_RESEARCH_PACK, _WORK_PACK, _COORDINATION_PACK)
_UNAVAILABLE_GAPS = frozenset(
    {
        "captured_object_unavailable",
        "event_payload_unavailable",
        "missing_ref",
        "redacted_event",
        "redacted_object",
        "unknown_event",
    }
)
_WORK_KINDS = frozenset(
    {
        FindingKind.ACTION_WITHOUT_RESULT,
        FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
        FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
        FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED,
        FindingKind.FAILED_WORK_OMITTED,
        FindingKind.LEDGER_STALE_OR_INCOMPLETE,
        FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED,
        FindingKind.RESULT_WITHOUT_ACTION,
        FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
        FindingKind.WEAK_OR_STALE_RESPONSE,
    }
)


SEMANTIC_REJECTED_REF_OUTSIDE_CASE: Final = "ref_outside_case"
SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM: Final = "hidden_source_claim"


class SemanticJudgmentRejected(ValueError):
    """The reviewer's judgment failed a structural fence and cannot become findings.

    Deliberately narrower than the module's other ``ValueError``s: the commit path catches exactly
    this, so a rejected judgment records ``invalid``/``semantic_judgment_rejected`` and still
    commits the deterministic findings, while a genuine coordinator bug keeps its old disposition.
    """


def _invalid(reason: str = "check_coordinator_invalid") -> ValueError:
    return ValueError(reason)


def _rejected(reason: str) -> SemanticJudgmentRejected:
    return SemanticJudgmentRejected(reason)


@dataclass(frozen=True, slots=True)
class SemanticJudgmentReview:
    """What the reviewer returned and what the post-validation fence did with it.

    ``rejected_by_reason`` is a sorted tuple of bounded ``(reason, count)`` pairs, never text from
    the challenge itself: it exists so "the reviewer answered and none of it reached you" is a
    countable fact on the check path instead of silence.
    """

    candidates: tuple[CandidateFinding, ...]
    challenges_returned: int
    rejected_by_reason: tuple[tuple[str, int], ...]

    def __post_init__(self) -> None:
        if (
            type(self.candidates) is not tuple
            or type(self.challenges_returned) is not int
            or self.challenges_returned < 0
            or type(self.rejected_by_reason) is not tuple
        ):
            raise _invalid("semantic_judgment_review_invalid")
        if any(type(pair) is not tuple or len(pair) != 2 for pair in self.rejected_by_reason):
            raise _invalid("semantic_judgment_review_invalid")
        if any(
            reason not in _SEMANTIC_REJECTION_REASONS or type(count) is not int or count < 1
            for reason, count in self.rejected_by_reason
        ):
            raise _invalid("semantic_judgment_review_invalid")
        # The diagnostic this value feeds claims that returned == accepted + rejected. Owning the
        # invariant here turns any future accounting drift into an immediate failure rather than a
        # durable count that quietly does not add up.
        if self.challenges_returned != len(self.candidates) + self.challenges_rejected:
            raise _invalid("semantic_judgment_review_invalid")

    @property
    def challenges_rejected(self) -> int:
        return sum(count for _reason, count in self.rejected_by_reason)


_SEMANTIC_REJECTION_REASONS: Final = frozenset(
    {SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM, SEMANTIC_REJECTED_REF_OUTSIDE_CASE}
)
_EMPTY_SEMANTIC_REVIEW: Final = SemanticJudgmentReview((), 0, ())


def _projected_finding_json(finding: Finding) -> JsonValue:
    """Adapt one encoded finding to the CHECK result's projected-finding shape.

    ``findings/finding-1.0.0`` leaves ``provenance`` simply absent on a deterministic finding, and
    ``finding_to_json`` honors that — it is the encoding events and receipt documents carry. The
    CHECK result's ``projected_finding`` is stricter: ``provenance`` is *required* and nullable, so
    a deterministic finding must present it as an explicit null. This mirrors the top-level
    ``semantic_provenance`` immediately below, which the same result already emits that way.
    """

    encoded = finding_to_json(finding)
    if "provenance" in encoded:
        return encoded
    return {**dict(encoded.items()), "provenance": None}


def check_awaiting_human_json(result: CheckAwaitingHuman) -> dict[str, JsonValue]:
    """Serialize the nonterminal CHECK branch: a continuation, never a verdict.

    No verdict, findings, coverage, or semantic provenance appear here. Emitting a
    completion-grade shape for a check that has not run would let a caller conclude from it.
    """

    continuation: dict[str, JsonValue] = {
        "kind": result.continuation.kind,
        "command": result.continuation.command,
        "replay_request_id": result.continuation.request_id,
        "instruction": result.continuation.instruction,
    }
    if result.continuation.pending_id is not None:
        continuation["pending_id"] = result.continuation.pending_id
    if result.continuation.expires_at is not None:
        continuation["expires_at"] = result.continuation.expires_at.wire
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": result.request_id,
        "ok": True,
        "state": "awaiting_human",
        "task_id": result.task_id,
        "session_id": result.session_id,
        "writer_id": result.writer_id,
        "subject_frontier": dict(result.subject_frontier.as_wire().items()),
        "result_frontier": dict(result.result_frontier.as_wire().items()),
        "semantic_status": SemanticStatus.AWAITING_HUMAN.value,
        "semantic_reason": SemanticReason.HUMAN_APPROVAL_REQUIRED.value,
        "continuation": continuation,
        "versions": {
            "protocol_version": result.versions.protocol_version,
            "engine_version": result.versions.engine_version,
            "projection_version": result.versions.projection_version,
            "policy_packs": result.versions.policy_packs,
        },
    }


def check_internal_json(result: CheckCommitResult) -> dict[str, JsonValue]:
    """Serialize sink-independent CHECK success without a privacy projection."""

    def children_json(children: CheckChildrenPreview) -> JsonValue:
        return {
            "label": children.label,
            "items": tuple(
                {
                    "child_task_id": item.child_task_id,
                    "origin": item.origin.value,
                    "acceptance": item.acceptance.value,
                    "work_state": item.work_state.value,
                    "session_health": item.session_health.value,
                    "rollup_state": item.rollup_state.value,
                    "blocking_conditions": item.blocking_conditions,
                }
                for item in children.items
            ),
            "tested_manifest_frontier": (
                None
                if children.tested_manifest_frontier is None
                else dict(children.tested_manifest_frontier.as_wire().items())
            ),
        }

    def advisory_notes_json(
        notes: tuple[CheckAdvisoryNote, ...],
    ) -> tuple[JsonValue, ...]:
        return tuple(
            {
                "kind": note.kind,
                "project_id": note.project_id,
                "task_ids": note.task_ids,
                "count": str(note.count),
            }
            for note in notes
        )

    return {
        "protocol_version": "0.1",
        "state": "complete",
        "schema_version": "1.0.0",
        "request_id": result.request_id,
        "ok": True,
        "task_id": result.task_id,
        "session_id": result.session_id,
        "writer_id": result.writer_id,
        "subject_frontier": dict(result.subject_frontier.as_wire().items()),
        "result_frontier": dict(result.result_frontier.as_wire().items()),
        "verdict": result.verdict.value,
        "findings": tuple(_projected_finding_json(item) for item in result.findings),
        "suppressed_count": str(result.suppressed_count),
        "policy_executions": tuple(
            {
                "policy_id": item.policy_id,
                "policy_version": item.policy_version,
                "outcome": item.outcome,
                "reason": item.reason,
            }
            for item in result.policy_executions
        ),
        "semantic_status": result.semantic_status.value,
        "semantic_reason": result.semantic_reason.value,
        "semantic_provenance": (
            None
            if result.semantic_provenance is None
            # Public Pydantic validation deliberately accepts a built-in dict here
            # before inspecting the safe identity fields.  The domain encoder
            # returns an immutable JsonObject, so thaw only its root container;
            # nested values remain canonical JSON mappings.
            else dict(semantic_provenance_to_json(result.semantic_provenance).items())
        ),
        "coverage": coverage_to_json(result.coverage),
        "versions": {
            "protocol_version": result.versions.protocol_version,
            "engine_version": result.versions.engine_version,
            "projection_version": result.versions.projection_version,
            "policy_packs": result.versions.policy_packs,
        },
        **({} if result.children is None else {"children": children_json(result.children)}),
        **(
            {}
            if not result.advisory_notes
            else {"advisory_notes": advisory_notes_json(result.advisory_notes)}
        ),
    }


def _lineage_preview(
    evaluation: LineageEvaluation,
    subject_frontier: Frontier,
) -> CheckChildrenPreview | None:
    """Adapt one frozen evaluator result to the additive CHECK child preview section."""

    if not evaluation.snapshots:
        return None
    snapshots = {item.child_task_id: item for item in evaluation.snapshots}
    items = tuple(
        CheckChildPreviewItem(
            child_task_id=str(rollup.child_task_id),
            origin=snapshots[rollup.child_task_id].origin,
            acceptance=snapshots[rollup.child_task_id].acceptance,
            work_state=snapshots[rollup.child_task_id].work_state,
            session_health=snapshots[rollup.child_task_id].session_health,
            rollup_state=rollup.state,
            blocking_conditions=rollup.blockers,
        )
        for rollup in evaluation.children
        if rollup.child_task_id in snapshots
    )
    if not items:
        return None
    label = (
        "preview"
        if any(item.later_manifest_ref is not None for item in evaluation.children)
        or any(gap.code == "lineage_manifest_uncovered" for gap in evaluation.gaps)
        else "recorded"
    )
    return CheckChildrenPreview(label, items, subject_frontier)


async def _lineage_at_frontier(
    runtime: TaskRuntime,
    frontier: Frontier,
    *,
    base_coverage: Coverage | None = None,
) -> LineageEvaluation:
    """Read only the parent ledger prefix named by a frozen check result."""

    records = tuple(
        [
            record
            async for record in runtime.ledger.load_events(
                runtime.session_id,
                through=frontier.sequence,
            )
        ]
    )
    if base_coverage is None:
        return evaluate_recorded_lineage(
            records,
            tested_through_sequence=frontier.sequence,
        )
    return evaluate_recorded_lineage(
        records,
        tested_through_sequence=frontier.sequence,
        base_coverage=base_coverage,
    )


async def _attach_replayed_lineage_preview(
    runtime: TaskRuntime,
    result: CheckCommitResult,
) -> CheckCommitResult:
    """Reconstruct the additive child preview from the exact recorded result prefix."""

    if result.children is not None:
        return result
    evaluation = await _lineage_at_frontier(
        runtime,
        result.subject_frontier,
        base_coverage=result.coverage,
    )
    preview = _lineage_preview(evaluation, result.subject_frontier)
    return result if preview is None else replace(result, children=preview)


type _AdvisoryFindingKey = tuple[
    str,
    str,
    str,
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]


async def _current_task_findings(
    app: Application,
    task_id: str,
) -> tuple[Finding, ...]:
    """Read only current unresolved finding identities from an admitted task ledger."""

    catalog = getattr(app, "start_catalog", None)
    runtime_port = getattr(app, "runtime", None)
    task_route = None if catalog is None else getattr(catalog, "task_route", None)
    route = None
    if not callable(task_route) or runtime_port is None:
        return ()
    try:
        route = await cast(Callable[[str], Awaitable[object | None]], task_route)(task_id)
        session_id = getattr(route, "session_id", None)
        if type(session_id) is not str:
            return ()
        child_runtime = await runtime_port.route(
            RouteCommand(
                session_id,
                None,
                RouteAccess.STRUCTURAL_READ,
                frozenset({RuntimeCapability.STRUCTURAL_READ}),
            )
        )
        if type(child_runtime) is not TaskRuntime or child_runtime.task_id != task_id:
            if type(child_runtime) is TaskRuntime:
                await runtime_port.release(child_runtime)
            return ()
        try:
            records: tuple[LedgerRecord, ...] = tuple(
                [
                    record
                    async for record in child_runtime.ledger.load_events(child_runtime.session_id)
                ]
            )
            projection = replay(records)
        finally:
            await runtime_port.release(child_runtime)
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="check",
            operation="project_advisory_finding_read",
        )
        return ()
    return tuple(
        finding
        for item in projection.findings.values()
        if not item.redacted
        and item.resolved_by_check_event_id is None
        and type(finding := item.payload) is Finding
    )


async def _current_task_structural_context(
    app: Application,
    task_id: str,
    project_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Return digest-only resource/plan identities for one admitted project member."""

    project_application = getattr(app, "project_application", None)
    coordination = (
        None
        if project_application is None
        else getattr(project_application, "coordination_runtime", None)
    )
    inputs = None if coordination is None else getattr(coordination, "inputs", None)
    input_for = None if inputs is None else getattr(inputs, "input_for", None)
    if not callable(input_for):
        return None
    try:
        declared = await cast(Callable[..., Awaitable[object | None]], input_for)(
            task_id,
            project_id,
        )
        if declared is None:
            return None
        resource_identities = getattr(declared, "resource_identities", None)
        plan_identities = getattr(declared, "plan_identities", None)
        if not callable(resource_identities) or not callable(plan_identities):
            return None
        resources_value: object = resource_identities()
        plans_value: object = plan_identities()
        if type(resources_value) is not tuple or type(plans_value) is not tuple:
            return None
        resources = cast(tuple[object, ...], resources_value)
        plans = cast(tuple[object, ...], plans_value)
        if any(type(item) is not str for item in (*resources, *plans)):
            return None
        resource_strings = cast(tuple[str, ...], resources)
        plan_strings = cast(tuple[str, ...], plans)
        return (
            tuple(sorted(set(resource_strings), key=str.encode)),
            tuple(sorted(set(plan_strings), key=str.encode)),
        )
    except Exception as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="check",
            operation="project_advisory_context_read",
        )
        return None


def _advisory_finding_key(
    finding: Finding,
    context: tuple[tuple[str, ...], tuple[str, ...]],
) -> _AdvisoryFindingKey | None:
    resources, plans = context
    if not resources and not plans:
        return None
    subject_kinds = tuple(
        sorted(
            {
                str(reference).split("_", 1)[0]
                for reference in finding.subject_refs
                if type(reference) is str and "_" in reference
            },
            key=str.encode,
        )
    )
    return (
        finding.kind.value,
        finding.policy_id,
        finding.policy_version,
        subject_kinds,
        resources,
        plans,
    )


async def _duplicate_project_advisory_task_ids(
    app: Application,
    requester_task_id: str,
    project_id: str,
    counterpart_ids: tuple[str, ...],
    findings: tuple[Finding, ...],
) -> tuple[str, ...]:
    """Find counterpart identities sharing a typed, resource-only finding signature."""

    requester_context = await _current_task_structural_context(app, requester_task_id, project_id)
    if requester_context is None:
        return ()
    own_keys = {
        key
        for finding in findings
        if (key := _advisory_finding_key(finding, requester_context)) is not None
    }
    if not own_keys:
        return ()
    duplicate_tasks: set[str] = set()
    for counterpart_id in counterpart_ids:
        context = await _current_task_structural_context(app, counterpart_id, project_id)
        if context is None:
            continue
        counterpart_findings = await _current_task_findings(app, counterpart_id)
        counterpart_keys = {
            key
            for finding in counterpart_findings
            if (key := _advisory_finding_key(finding, context)) is not None
        }
        if own_keys & counterpart_keys:
            duplicate_tasks.add(counterpart_id)
    if not duplicate_tasks:
        return ()
    return tuple(sorted({requester_task_id, *duplicate_tasks}, key=str.encode))


_PROJECT_ADVICE_AUTHORITY_REFUSALS: Final = frozenset(
    {
        CoordinationErrorCode.CONSENT_REQUIRED,
        CoordinationErrorCode.GRANT_REQUIRED,
        CoordinationErrorCode.GRANT_REVOKED,
        CoordinationErrorCode.GENERATION_MISMATCH,
        CoordinationErrorCode.PROJECT_NOT_FOUND,
        CoordinationErrorCode.PROJECT_DISSOLVED,
        CoordinationErrorCode.MEMBER_NOT_FOUND,
    }
)


async def _current_project_advisory_notes(
    app: Application,
    task_id: str,
    findings: tuple[Finding, ...],
) -> tuple[CheckAdvisoryNote, ...]:
    """Project current, consented coordination advice beside a recorded check result.

    Coordination advice is a projection-time observation.  It is deliberately looked up after
    the ledger check has frozen/committed its deterministic result, and it is never passed to the
    ranking or ledger commit paths.  ``ProjectApplication.live_admitted_member_task_ids`` is the
    admission boundary here: it rechecks source consent, membership, grant, task liveness, and
    current project generation before returning counterpart identities, so a revoked generation
    fails closed. Recorded overlap advice can supply candidate identities, but every result still
    requires the current member-admission boundary at the same captured generation.
    """

    project_application = getattr(app, "project_application", None)
    catalog = getattr(project_application, "catalog", None)
    list_projects = None if catalog is None else getattr(catalog, "list_task_project_ids", None)
    project_state = None if catalog is None else getattr(catalog, "project_state", None)
    live_members_for = (
        None
        if project_application is None
        else getattr(project_application, "live_admitted_member_task_ids", None)
    )
    advice_for = (
        None
        if project_application is None
        else getattr(project_application, "coordination_advice_for", None)
    )
    if not callable(list_projects) or not callable(project_state) or not callable(live_members_for):
        return ()
    try:
        project_ids = await cast(Callable[[str], Awaitable[tuple[str, ...]]], list_projects)(
            task_id
        )
    except Exception as exc:
        # Advice is additive and optional.  A projection failure must not change the deterministic
        # check outcome or strand an already committed operation; it simply produces no note.
        record_unexpected_exception_without_raising(
            exc,
            component="check",
            operation="project_advisory_membership_read",
        )
        return ()

    notes: list[CheckAdvisoryNote] = []
    valid_project_ids = tuple(item for item in project_ids if type(item) is str)
    for project_id in sorted(set(valid_project_ids), key=str.encode):
        try:
            descriptor = await cast(Callable[[str], Awaitable[object | None]], project_state)(
                project_id
            )
            generation = getattr(descriptor, "membership_generation", None)
            if type(generation) is not int or generation < 1:
                continue
        except Exception:
            continue
        counterpart_ids: tuple[str, ...] = ()
        live_lookup_failed = False
        if callable(live_members_for):
            try:
                counterpart_ids = tuple(
                    sorted(
                        {
                            member
                            for member in await cast(
                                Callable[..., Awaitable[tuple[str, ...]]], live_members_for
                            )(task_id, project=project_id, expected_generation=generation)
                            if type(member) is str and member != task_id
                        },
                        key=str.encode,
                    )
                )
            except Exception as exc:
                if (
                    isinstance(exc, CoordinationError)
                    and exc.code in _PROJECT_ADVICE_AUTHORITY_REFUSALS
                ):
                    # A missing or revoked authority is an expected absence of optional advice.
                    # Do not retry it through the compatibility fallback or diagnose corruption.
                    continue
                live_lookup_failed = True
                # A single stale or revoked project cannot suppress notes for other independently
                # admitted projects, and never changes the recorded check verdict.
                record_unexpected_exception_without_raising(
                    exc,
                    component="check",
                    operation="project_advisory_read",
                )
        if (
            (not callable(live_members_for) or live_lookup_failed)
            and not counterpart_ids
            and callable(advice_for)
        ):
            # Compatibility fallback for pre-B3 compositions: this remains generation/consent
            # fenced but only knows about projects for which overlap advice was already delivered.
            try:
                rows = await cast(Callable[..., Awaitable[tuple[object, ...]]], advice_for)(
                    task_id, project=project_id, expected_generation=generation
                )
                counterpart_ids = tuple(
                    sorted(
                        {
                            counterpart
                            for row in rows
                            if getattr(row, "target_task_id", None) == task_id
                            and getattr(row, "project_id", None) == project_id
                            and type(counterpart := getattr(row, "counterpart_task_id", None))
                            is str
                            and counterpart != task_id
                        },
                        key=str.encode,
                    )
                )
            except Exception as exc:
                if (
                    isinstance(exc, CoordinationError)
                    and exc.code in _PROJECT_ADVICE_AUTHORITY_REFUSALS
                ):
                    continue
                record_unexpected_exception_without_raising(
                    exc,
                    component="check",
                    operation="project_advisory_read",
                )
        if counterpart_ids and callable(live_members_for):
            try:
                current_members = tuple(
                    sorted(
                        {
                            member
                            for member in await cast(
                                Callable[..., Awaitable[tuple[str, ...]]], live_members_for
                            )(task_id, project=project_id, expected_generation=generation)
                            if type(member) is str and member != task_id
                        },
                        key=str.encode,
                    )
                )
            except Exception:
                # The second admission read is a revoke/generation fence.  A race here must
                # suppress this projection rather than deliver identities from the prior page.
                counterpart_ids = ()
            else:
                if current_members != counterpart_ids:
                    counterpart_ids = ()
        if counterpart_ids:
            duplicate_task_ids = await _duplicate_project_advisory_task_ids(
                app,
                task_id,
                project_id,
                counterpart_ids,
                findings,
            )
            if callable(live_members_for):
                try:
                    final_members = tuple(
                        sorted(
                            {
                                member
                                for member in await cast(
                                    Callable[..., Awaitable[tuple[str, ...]]], live_members_for
                                )(task_id, project=project_id, expected_generation=generation)
                                if type(member) is str and member != task_id
                            },
                            key=str.encode,
                        )
                    )
                except Exception:
                    # Duplicate context and ledger reads can await long enough for source consent,
                    # a recipient grant, or the project generation to change.  A failed final
                    # admission read must suppress both notes from this stale projection.
                    counterpart_ids = ()
                    duplicate_task_ids = ()
                else:
                    if final_members != counterpart_ids:
                        counterpart_ids = ()
                        duplicate_task_ids = ()
            if counterpart_ids:
                try:
                    notes.append(
                        CheckAdvisoryNote(
                            "live_member_present",
                            project_id,
                            counterpart_ids,
                            len(counterpart_ids),
                        )
                    )
                except TypeError, ValueError:
                    # IDs are validated again at the structural result boundary.  An invalid adapter
                    # row is not allowed to become a public note or affect the check itself.
                    pass
            if counterpart_ids and duplicate_task_ids:
                try:
                    notes.append(
                        CheckAdvisoryNote(
                            "duplicate_finding",
                            project_id,
                            duplicate_task_ids,
                            len(duplicate_task_ids),
                        )
                    )
                except TypeError, ValueError:
                    pass
        if len(notes) >= 64:
            break
    return tuple(notes)


async def _attach_current_project_advisory_notes(
    app: Application,
    task_id: str,
    result: CheckCommitResult,
) -> CheckCommitResult:
    """Attach current advice without changing the frozen result or replay semantics."""

    notes = await _current_project_advisory_notes(app, task_id, result.findings)
    return result if result.advisory_notes == notes else replace(result, advisory_notes=notes)


@dataclass(frozen=True, slots=True)
class CheckScope:
    claim_ids: tuple[str, ...]
    obligation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        claims = tuple(claim_id(value) for value in self.claim_ids)
        obligations = tuple(obligation_id(value) for value in self.obligation_ids)
        if claims != tuple(sorted(set(claims), key=str.encode)):
            raise _invalid("check_scope_invalid")
        if obligations != tuple(sorted(set(obligations), key=str.encode)):
            raise _invalid("check_scope_invalid")
        object.__setattr__(self, "claim_ids", cast(tuple[str, ...], claims))
        object.__setattr__(self, "obligation_ids", cast(tuple[str, ...], obligations))

    @property
    def roots(self) -> frozenset[str]:
        return frozenset((*self.claim_ids, *self.obligation_ids))

    @property
    def whole_case(self) -> bool:
        return not self.claim_ids and not self.obligation_ids


def normalize_check_scope(request: CheckRequest) -> CheckScope:
    """Normalize omitted and explicit-empty scope to the same immutable value."""

    scope = request.scope
    if scope is None:
        return CheckScope((), ())
    return CheckScope(
        tuple(sorted(scope.claim_ids, key=str.encode)),
        tuple(sorted(scope.obligation_ids, key=str.encode)),
    )


@dataclass(frozen=True, slots=True)
class FinalSemanticEvaluation:
    status: SemanticStatus
    reason: SemanticReason
    judgment: SemanticJudgment | None = None
    provenance: SemanticProvenance | None = None
    # Bounded structural attempt accounting reconstructed from durable rows when a job ran.
    # Not part of the frozen public check-result wire; owner recovery reads the ledger.
    attempt_accounting: object | None = None
    # When the durable semantic phase renewed the check operation lease (lease TTL is 60s while
    # timeout_seconds may be longer), later phase advance / commit must use this CAS fence.
    operation_lease: OperationLease | None = None
    # Categories the review profile selected that the inference channel did not permit. A review
    # can succeed while structurally unable to answer its own question; coverage must say so.
    withheld_review_categories: tuple[str, ...] = ()
    # True when composing the case had to shorten or replace recorded text that the publish-side
    # prose bound had already accepted. The reviewer judged a fragment; coverage must say so
    # rather than let the shortening pass as material the author chose not to send.
    case_content_over_item_limit: bool = False
    case_reference_scope_reduced: bool = False
    # Set only on the nonterminal awaiting_human branch: what the caller must do to resume this
    # exact request. Every terminal outcome leaves it None. A one-use disclosure wait keeps its
    # job and attempt open; a missing standing repository grant stops before either exists.
    continuation: SemanticContinuation | None = None

    def __post_init__(self) -> None:
        validate_semantic_outcome(self.status, self.reason)
        validate_semantic_provenance_binding(
            self.status,
            self.reason,
            None if self.provenance is None else self.provenance.status,
            None if self.provenance is None else self.provenance.reason,
        )
        if self.provenance is not None and type(self.provenance) is not SemanticProvenance:
            raise _invalid("semantic_provenance_invalid")
        if self.status is SemanticStatus.SUCCEEDED:
            if type(self.judgment) is not SemanticJudgment or self.provenance is None:
                raise _invalid("semantic_judgment_invalid")
        elif self.judgment is not None:
            raise _invalid("semantic_judgment_invalid")
        if self.operation_lease is not None and type(self.operation_lease) is not OperationLease:
            raise _invalid("operation_lease_invalid")
        if self.continuation is not None:
            if type(self.continuation) is not SemanticContinuation:
                raise _invalid("semantic_continuation_invalid")
            # A continuation on a terminal outcome would tell the caller to resume a request the
            # ledger has already closed. Bind it to the one status that keeps the job open.
            if self.status is not SemanticStatus.AWAITING_HUMAN:
                raise _invalid("semantic_continuation_invalid")


# Gaps that record a semantic review the task actually attempted and did not get. They are the
# environment's account of the missing review, never the caller's; `semantic_review_not_requested`
# is deliberately absent because it is the one this set exists to disambiguate.
# `optional_semantic_review_registration_drift` is deliberately absent too: it is re-added
# fresh on the strict-ceiling path only after reading the live applied-route record, so
# carrying it would let a stale drift claim survive a `mcp remove` (which clears the
# record) or a strict reinstall on a later deterministic-only successor (issue #537).
_SEMANTIC_ATTEMPT_GAPS: Final = frozenset(
    {
        "semantic_case_capacity_exceeded",
        OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
        SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    }
)


def _strict_ceiling_route_drift(
    *, host_profile: McpHostProfile = "generic", _state: Path | None
) -> bool:
    """Fail-soft applied-policy probe for the strict route ceiling (issue #537).

    True only when the durable applied-route record says the last install applied the
    policy route while this check serves the strict ceiling: the serving process is
    stale, not the privacy posture. Any missing, corrupt, unsafe, or non-policy record
    reads as no drift. Never raises, never logs content, never changes the terminal
    status/reason/provenance — the caller only adds a structural coverage gap.
    """

    # A bare or otherwise generic serving command does not prove that Codex owns the process.
    # The applied-route record is Codex-specific, so comparing it against another host would
    # manufacture a registration-drift claim (issue #548).
    if host_profile != "codex":
        return False
    try:
        from yoetz.application.applied_mcp_route import read_applied_route

        record = read_applied_route(_state=_state)
    except Exception:
        return False
    return isinstance(record, dict) and record.get("applied_profile") == "policy"


def carried_semantic_attempt_gaps(case: DeterministicCase, status: SemanticStatus) -> set[str]:
    """Carry a superseded check's semantic-attempt gap onto a deterministic-only successor.

    A blocked or unavailable semantic review is normally followed by a ``deterministic_only``
    re-check, which is the stop-rule behaviour: a blocked review is a coverage gap, not a retry
    problem. That successor replaces ``latest_tested_state`` wholesale, so without this the only
    surviving disclosure is ``semantic_review_not_requested`` -- which attributes the missing
    review to the agent not asking, when the environment refused (issue #185). Carrying the
    earlier gap forward keeps the receipt's account of *why* there is no semantic review.

    The carry is task-level, not scope-bound: ``LatestTestedState`` records no scope, and the
    successor replaces it wholesale whatever its scope, so a gap from a differently-scoped
    attempt is inherited too. That errs toward disclosing a refusal the task did experience,
    never toward claiming coverage.
    """

    # Only a check that did not itself request review can inherit; an attempt of its own already
    # states its own outcome, and a succeeded review has closed the gap rather than carried it.
    if status is not SemanticStatus.NOT_REQUESTED:
        return set()
    latest = case.projection.latest_tested_state
    if latest is None:
        return set()
    return set(_SEMANTIC_ATTEMPT_GAPS & set(latest.coverage.known_gaps))


class _VerificationPolicy(Protocol):
    @property
    def semantic(self) -> Literal["disabled", "optional", "required"]: ...

    @property
    def max_findings(self) -> int: ...

    @property
    def default_check_mode(
        self,
    ) -> Literal["deterministic_only", "semantic_if_configured", "semantic_required"]: ...


class Application(Protocol):
    @property
    def runtime(self) -> BundleRuntimePort: ...

    @property
    def clock(self) -> ClockPort: ...

    @property
    def ids(self) -> IdPort: ...

    @property
    def verification_policy(self) -> _VerificationPolicy: ...

    @property
    def reconcile_observation_capture(
        self,
    ) -> Callable[[TaskRuntime], Awaitable[None]] | None: ...

    async def evaluate_semantic_check(
        self,
        frozen: FrozenCase,
        deterministic_findings: tuple[Finding, ...],
        runtime: TaskRuntime | None = None,
        lineage_evaluation: LineageEvaluation | None = None,
    ) -> FinalSemanticEvaluation: ...


type _PolicyEvaluator = Callable[[DeterministicCase], tuple[DeterministicAssessment, ...]]


@dataclass(frozen=True, slots=True)
class _DurableDeterministicResult:
    findings: tuple[Finding, ...]
    executions: tuple[CheckPolicyExecution, ...]


class _DeterministicCheckpointSuperseded(Exception):
    """The persisted deterministic result predates the current finding-text contract.

    The checkpoint's bindings verified, so this is not corruption: the same digest-verified
    frozen case is still available and the deterministic phase recomputes from it instead of
    failing the request (issue #340).
    """


_DETERMINISTIC_RESULT_KEYS: Final = frozenset(
    {
        "schema_version",
        "request_id",
        "request_digest",
        "task_id",
        "session_id",
        "writer_id",
        "subject_frontier",
        "dependency_digest",
        "text_contract_digest",
        "prior_resume",
        "policy_executions",
        "assessments",
    }
)
# Checkpoints written before the text-contract stamp existed. Their wording generation is
# unknowable, so they are always superseded, never validated byte-for-byte.
_LEGACY_DETERMINISTIC_RESULT_KEYS: Final = _DETERMINISTIC_RESULT_KEYS - {"text_contract_digest"}


def _object_pointer(ref: ObjectRef) -> dict[str, JsonValue]:
    return {
        "object_id": ref.object_id,
        "envelope_digest": ref.envelope_digest,
        "commitment": ref.commitment,
    }


async def _read_all(ref: ObjectRef, runtime: TaskRuntime) -> bytes:
    return b"".join([chunk async for chunk in runtime.objects.open_verified(ref)])


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("deterministic_result_shape_invalid")
    source = cast(Mapping[object, object], value)
    if any(type(key) is not str for key in source):
        raise ValueError("deterministic_result_shape_invalid")
    return cast(Mapping[str, object], source)


async def _load_deterministic_result(
    runtime: TaskRuntime,
    request: CheckRequest,
    request_digest_value: str,
    frozen: FrozenCase,
) -> _DurableDeterministicResult:
    record = await runtime.ledger.lookup_operation(request.writer_id, request.request_id)
    try:
        if (
            type(record) is not OperationRecord
            or record.state is not OperationState.PENDING
            or record.phase is CheckPhase.RESERVED
            or record.request_digest != request_digest_value
            or record.resume_object_ref is None
            or record.resume_object_ref.metadata.kind is not ObjectKind.DETERMINISTIC_RESULT
        ):
            raise ValueError("deterministic_result_binding_invalid")
        ref = record.resume_object_ref
        raw = await _read_all(ref, runtime)
        parsed = strict_json_parse(raw)
        if canonical_encode(parsed) != raw:
            raise ValueError("deterministic_result_noncanonical")
        source = _mapping(parsed)
        if frozenset(source) not in {
            _DETERMINISTIC_RESULT_KEYS,
            _LEGACY_DETERMINISTIC_RESULT_KEYS,
        }:
            raise ValueError("deterministic_result_shape_invalid")
        if (
            source["schema_version"] != "1.0.0"
            or source["request_id"] != request.request_id
            or source["request_digest"] != request_digest_value
            or source["task_id"] != runtime.task_id
            or source["session_id"] != runtime.session_id
            or source["writer_id"] != runtime.writer_id
            or source["subject_frontier"] != frozen.case.frontier.as_wire()
            or source["dependency_digest"] != frozen.lease.dependency_digest
        ):
            raise ValueError("deterministic_result_binding_invalid")
        pointer = _mapping(source["prior_resume"])
        if frozenset(pointer) != frozenset({"object_id", "envelope_digest", "commitment"}):
            raise ValueError("deterministic_result_pointer_invalid")
        prior = await runtime.objects.resolve_verified(
            cast(str, pointer["object_id"]), cast(str, pointer["envelope_digest"])
        )
        if (
            prior.metadata.kind is not ObjectKind.CHECK_RESUME
            or prior.metadata.task_id != runtime.task_id
            or prior.commitment != pointer["commitment"]
        ):
            raise ValueError("deterministic_result_pointer_invalid")
        await _read_all(prior, runtime)
        # Defer supersession until every persisted result field has been decoded and validated.
        # A stale stamp does not make otherwise malformed checkpoint content safe to ignore.
        checkpoint_superseded = (
            source.get("text_contract_digest") != DETERMINISTIC_TEXT_CONTRACT_DIGEST
        )
        raw_executions = source["policy_executions"]
        raw_assessments = source["assessments"]
        if type(raw_executions) is not list or type(raw_assessments) is not list:
            raise ValueError("deterministic_result_shape_invalid")
        execution_values = cast(list[object], raw_executions)
        assessment_values = cast(list[object], raw_assessments)
        executions = tuple(
            CheckPolicyExecution(
                cast(str, item["policy_id"]),
                cast(str, item["policy_version"]),
                cast(Literal["run", "skipped", "failed"], item["outcome"]),
                cast(
                    Literal[
                        "completed",
                        "material_unavailable",
                        "not_applicable",
                        "policy_failure",
                        "scope_excluded",
                    ],
                    item["reason"],
                ),
            )
            for item in (_mapping(value) for value in execution_values)
            if frozenset(item) == frozenset({"policy_id", "policy_version", "outcome", "reason"})
        )
        if len(executions) != len(execution_values):
            raise ValueError("deterministic_result_execution_invalid")
        findings: list[Finding] = []
        for raw_assessment in assessment_values:
            item = _mapping(raw_assessment)
            if frozenset(item) != frozenset({"finding", "basis"}):
                raise ValueError("deterministic_result_assessment_invalid")
            finding = finding_from_json(freeze_json(item["finding"]))
            basis = finding_basis_from_json(freeze_json(item["basis"]))
            candidate = CandidateFinding(
                finding.kind,
                finding.origin,
                finding.priority,
                finding.summary,
                finding.detail,
                finding.subject_refs,
                finding.policy_id,
                finding.policy_version,
                finding.subject_frontier,
                finding.coverage,
                finding.provenance,
            )
            # A matching contract stamp with drifted text means a wording branch the contract
            # corpus missed. Keep validating the finding and basis, but defer recomputation
            # until every assessment has been checked.
            rendered_text = render_deterministic_finding_text(
                finding.kind,
                finding.subject_refs,
                basis.coverage_gaps,
                basis.observed_facts,
            )
            if (finding.summary, finding.detail) != rendered_text:
                checkpoint_superseded = True
                # Validate the deterministic assessment against the current rendering while
                # preserving the stored finding's structural fields for Finding validation.
                candidate = CandidateFinding(
                    finding.kind,
                    finding.origin,
                    finding.priority,
                    rendered_text[0],
                    rendered_text[1],
                    finding.subject_refs,
                    finding.policy_id,
                    finding.policy_version,
                    finding.subject_frontier,
                    finding.coverage,
                    finding.provenance,
                )
            DeterministicAssessment(candidate, basis)
            findings.append(finding)
        if len({item.finding_id for item in findings}) != len(findings):
            raise ValueError("deterministic_result_finding_invalid")
        if checkpoint_superseded:
            raise _DeterministicCheckpointSuperseded()
        return _DurableDeterministicResult(tuple(findings), executions)
    except PublicOperationError, _DeterministicCheckpointSuperseded:
        raise
    except Exception as exc:
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT,
            "The deterministic checkpoint is corrupt.",
            False,
        ) from exc


def _selected_packs(request: CheckRequest) -> tuple[str, ...]:
    selected = _CANONICAL_PACKS if request.policy_packs is None else tuple(request.policy_packs)
    if not selected:
        raise _invalid("check_policy_selection_invalid")
    normalized = tuple(sorted(set(selected), key=str.encode))
    if len(normalized) != len(selected) or any(pack not in _CANONICAL_PACKS for pack in normalized):
        raise _invalid("check_policy_selection_invalid")
    return normalized


def _pack_identity(pack: str) -> tuple[str, str]:
    policy_id, version = pack.split("/", maxsplit=1)
    return policy_id, version


def _pack_roots(case: DeterministicCase, pack: str) -> frozenset[str]:
    if pack == _RESEARCH_PACK:
        collections = (
            case.projection.claims,
            case.projection.results,
            case.projection.evidence,
            case.projection.findings,
        )
    elif pack == _WORK_PACK:
        collections = (
            case.projection.obligations,
            case.projection.claims,
            case.projection.actions,
            case.projection.results,
            case.projection.evidence,
            case.projection.findings,
            case.projection.responses,
        )
    else:
        collections = (
            case.projection.coordination_contexts,
            case.projection.coordination_declarations,
            case.projection.coordination_dispositions,
        )
    return frozenset(str(value) for collection in collections for value in collection)


def _scope_execution(
    case: DeterministicCase,
    scope: CheckScope,
    pack: str,
) -> CheckPolicyExecution | None:
    policy_id, version = _pack_identity(pack)
    roots = _pack_roots(case, pack)
    if scope.whole_case:
        if not roots:
            return CheckPolicyExecution(policy_id, version, "skipped", "not_applicable")
        selected = roots
    else:
        selected = roots & scope.roots
        if not selected:
            return CheckPolicyExecution(policy_id, version, "skipped", "scope_excluded")
    available = tuple(case.coverage_by_ref.get(cast(FindingBasisRef, root)) for root in selected)
    if available and all(
        coverage is None or _UNAVAILABLE_GAPS & set(coverage.known_gaps) for coverage in available
    ):
        return CheckPolicyExecution(policy_id, version, "skipped", "material_unavailable")
    return None


def _response_identity(
    assessment: DeterministicAssessment,
    fact_code: str,
) -> tuple[FindingBasisRef, ...] | None:
    """Return the (finding, response event, admissible evidence) refs a response rule reported."""

    for fact in assessment.basis.observed_facts:
        if fact.fact_code == fact_code:
            return fact.subject_refs
    return None


def _collapse_response_overlap(
    assessments: tuple[DeterministicAssessment, ...],
) -> tuple[DeterministicAssessment, ...]:
    """Drop the work-integrity response finding when research-evidence reported the same response.

    A current unsupported rejection or waiver of a deterministic finding satisfies both
    ``weak_or_stale_response`` and ``questionable_finding_rejection``. Each pack is a closed rule
    table that cannot see the other, so the collapse belongs here, where it is known which packs
    actually ran. Keying on the assessment research-evidence really produced -- rather than
    re-deriving its predicate inside work-integrity -- means a research pack that was deselected,
    scope-excluded, skipped as unavailable, or that failed leaves the work-integrity finding
    standing instead of silently losing it.
    """

    rejected = {
        identity
        for item in assessments
        if item.candidate.kind is FindingKind.QUESTIONABLE_FINDING_REJECTION
        and (identity := _response_identity(item, RESEARCH_REJECTION_PRESENT_FACT)) is not None
    }
    if not rejected:
        return assessments
    return tuple(
        item
        for item in assessments
        if item.candidate.kind is not FindingKind.WEAK_OR_STALE_RESPONSE
        or _response_identity(item, WORK_RESPONSE_PRESENT_FACT) not in rejected
    )


def run_deterministic_policies(
    case: DeterministicCase,
    scope: CheckScope,
    packs: tuple[str, ...],
    *,
    evaluators: dict[str, _PolicyEvaluator] | None = None,
) -> tuple[tuple[DeterministicAssessment, ...], tuple[CheckPolicyExecution, ...]]:
    """Run the selected built-ins with application-owned scope and execution accounting."""

    if type(case) is not DeterministicCase or type(scope) is not CheckScope:
        raise _invalid()
    registered: dict[str, _PolicyEvaluator] = {
        _RESEARCH_PACK: research_evidence_findings,
        _WORK_PACK: work_integrity_findings,
    }
    if evaluators is not None:
        for key, evaluator in evaluators.items():
            registered[key] = evaluator
    executions: list[CheckPolicyExecution] = []
    by_pack: dict[str, tuple[DeterministicAssessment, ...]] = {}
    for pack in packs:
        skip = _scope_execution(case, scope, pack)
        if skip is not None:
            executions.append(skip)
            by_pack[pack] = ()
            continue
        policy_id, version = _pack_identity(pack)
        if pack == _COORDINATION_PACK:
            # Coordination assessments are supplied by the application runtime from the same
            # frozen recipient projection immediately before this function's caller allocates
            # finding IDs.  Keeping the execution row here makes selection/coverage explicit
            # without consulting live catalog state from this pure dispatcher.
            executions.append(CheckPolicyExecution(policy_id, version, "run", "completed"))
            by_pack[pack] = ()
            continue
        try:
            evaluated = registered[pack](case)
            if not scope.whole_case:
                evaluated = tuple(
                    item
                    for item in evaluated
                    if not scope.roots.isdisjoint(map(str, item.candidate.subject_refs))
                )
        except Exception:
            executions.append(CheckPolicyExecution(policy_id, version, "failed", "policy_failure"))
            by_pack[pack] = ()
        else:
            executions.append(CheckPolicyExecution(policy_id, version, "run", "completed"))
            by_pack[pack] = evaluated

    # Finding emission order is intentionally distinct from execution accounting order.
    assessments = _collapse_response_overlap(
        by_pack.get(_WORK_PACK, ()) + by_pack.get(_RESEARCH_PACK, ())
    )
    keys = tuple(
        (item.candidate.policy_id, item.basis.rule_id, item.candidate.subject_refs)
        for item in assessments
    )
    if len(keys) != len(set(keys)):
        raise _invalid("duplicate_deterministic_assessment")
    return assessments, tuple(executions)


async def _coordination_assessments_for_frozen_case(
    app: Application,
    runtime: TaskRuntime,
    case: DeterministicCase,
    scope: CheckScope,
    packs: tuple[str, ...],
) -> tuple[DeterministicAssessment, ...]:
    """Ask the composed coordination runtime to assess only frozen recipient facts."""

    if _COORDINATION_PACK not in packs:
        return ()
    project_application = getattr(app, "project_application", None)
    coordinator = getattr(project_application, "coordination_runtime", None)
    assess = getattr(coordinator, "assessments_for_check", None)
    if not callable(assess):
        return ()
    pending = assess(
        runtime.task_id,
        case,
        scope_roots=scope.roots,
        whole_case=scope.whole_case,
    )
    if not inspect.isawaitable(pending):
        raise _invalid("coordination_assessment_invalid")
    result = await cast(Awaitable[object], pending)
    values = cast(tuple[object, ...], result)
    if type(result) is not tuple or any(
        type(item) is not DeterministicAssessment for item in values
    ):
        raise _invalid("coordination_assessment_invalid")
    return cast(tuple[DeterministicAssessment, ...], result)


FindingIdentity = tuple[FindingKind, str, tuple[EventId | ObligationId | ClaimId, ...]]


def prior_finding_ids(projection: ProjectionState) -> dict[FindingIdentity, FindingId]:
    """Index the live recorded findings by the identity a policy re-derives them under.

    A finding a later qualifying check already resolved is history, not a live row: an issue that
    fires again after that proof is a successor and takes a fresh ID, so the resolved row keeps
    its proof and the new row starts unresolved (issue #458).
    """

    prior: dict[FindingIdentity, FindingId] = {}
    for key, record in projection.findings.items():
        if record.payload is None or record.resolved_by_check_event_id is not None:
            continue
        prior[(record.payload.kind, record.payload.policy_id, record.payload.subject_refs)] = key
    return prior


def allocate_findings(
    ids: IdPort,
    candidates: tuple[CandidateFinding, ...],
    prior: Mapping[FindingIdentity, FindingId] | None = None,
) -> tuple[Finding, ...]:
    """Allocate stable IDs in deterministic candidate order.

    A candidate that re-derives a live recorded finding keeps that finding's ID, so a re-check
    converges on the record already answered instead of minting an unresolvable duplicate.
    """

    output: list[Finding] = []
    for candidate in candidates:
        if type(candidate) is not CandidateFinding:
            raise _invalid("finding_candidate_invalid")
        existing = (
            None
            if prior is None
            else prior.get((candidate.kind, candidate.policy_id, candidate.subject_refs))
        )
        output.append(
            Finding(
                existing if existing is not None else finding_id(ids.new(IdKind.FINDING)),
                candidate.kind,
                candidate.origin,
                candidate.priority,
                candidate.summary,
                candidate.detail,
                candidate.subject_refs,
                candidate.policy_id,
                candidate.policy_version,
                candidate.subject_frontier,
                candidate.coverage,
                candidate.provenance,
            )
        )
    return tuple(output)


async def _publish_deterministic_result(
    app: Application,
    runtime: TaskRuntime,
    request: CheckRequest,
    frozen: FrozenCase,
    assessments: tuple[DeterministicAssessment, ...],
    findings: tuple[Finding, ...],
    executions: tuple[CheckPolicyExecution, ...],
    request_digest_value: str,
) -> FrozenCase:
    """Pin local finding identities and bases before optional semantic work."""

    if frozen.lease.phase is not CheckPhase.RESERVED:
        raise PublicOperationError(
            PublicErrorCode.OPERATION_PENDING,
            "The check operation is pending.",
            True,
        )
    if len(assessments) != len(findings):
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT,
            "The deterministic result is inconsistent.",
            False,
        )
    operation = await runtime.ledger.lookup_operation(request.writer_id, request.request_id)
    if (
        type(operation) is not OperationRecord
        or operation.state is not OperationState.PENDING
        or operation.phase is not CheckPhase.RESERVED
        or operation.request_digest != request_digest_value
        or operation.resume_object_ref is None
        or operation.resume_object_ref.metadata.kind is not ObjectKind.CHECK_RESUME
    ):
        raise PublicOperationError(
            PublicErrorCode.STORAGE_CORRUPT,
            "The check resume checkpoint is inconsistent.",
            False,
        )
    prior_resume = operation.resume_object_ref
    canonical = canonical_encode(
        cast(
            JsonValue,
            {
                "schema_version": "1.0.0",
                "request_id": request.request_id,
                "request_digest": request_digest_value,
                "task_id": runtime.task_id,
                "session_id": runtime.session_id,
                "writer_id": runtime.writer_id,
                "subject_frontier": dict(frozen.case.frontier.as_wire().items()),
                "dependency_digest": frozen.lease.dependency_digest,
                "text_contract_digest": DETERMINISTIC_TEXT_CONTRACT_DIGEST,
                "prior_resume": _object_pointer(prior_resume),
                "policy_executions": tuple(
                    {
                        "policy_id": item.policy_id,
                        "policy_version": item.policy_version,
                        "outcome": item.outcome,
                        "reason": item.reason,
                    }
                    for item in executions
                ),
                "assessments": tuple(
                    {
                        "finding": finding_to_json(finding),
                        "basis": finding_basis_to_json(assessment.basis),
                    }
                    for assessment, finding in zip(assessments, findings, strict=True)
                ),
            },
        )
    )
    metadata = ObjectMetadata(
        ObjectKind.DETERMINISTIC_RESULT,
        "application/vnd.yoetz.deterministic-result+json",
        runtime.task_id,
        app.clock.now_utc(),
    )
    staged = await runtime.objects.stage(
        ObjectSource(data=canonical, declared_size=len(canonical)),
        metadata,
    )
    result_ref = await runtime.objects.finalize(staged)
    lease = await runtime.ledger.advance_check_phase(
        frozen.lease,
        CheckPhase.RESERVED,
        CheckPhase.LOCAL_READY,
        result_ref,
    )
    return FrozenCase(frozen.case, lease)


def _policy_identity(kind: FindingKind) -> tuple[str, str]:
    return (
        ("work-integrity", "0.1.0")
        if kind in _WORK_KINDS
        else ("coordination", "0.1.0")
        if kind is FindingKind.COORDINATION_OVERLAP
        else (
            "research-evidence",
            "0.1.0",
        )
    )


def _resolve_challenge_refs(
    case: DeterministicCase,
    deterministic: tuple[Finding, ...],
    challenge: ReviewerChallenge,
) -> tuple[str, ...] | None:
    """Resolve one challenge's citations to frozen subject refs, or ``None`` if any is outside.

    Every per-ref test below is byte-identical to the fence this replaced; only the disposition of
    a failure changed, from raising (which discarded the entire judgment, and with it the check)
    to returning ``None`` so the caller can drop this one challenge and count it.
    """

    findings = {str(item.finding_id): item for item in deterministic}
    resolved: set[str] = set()
    for ref in challenge.cited_refs:
        if ref.startswith("fnd_"):
            finding = findings.get(ref)
            if finding is None:
                return None
            resolved.update(map(str, finding.subject_refs))
        elif ref not in case.allowed_ids:
            return None
        elif ref.startswith(("evt_", "obl_", "clm_")):
            resolved.add(ref)
        else:
            source = None
            if ref.startswith("act_"):
                from yoetz.domain.values import action_id

                record = case.projection.actions.get(action_id(ref))
                source = None if record is None else str(record.source_event_id)
            elif ref.startswith("res_"):
                from yoetz.domain.values import result_id

                record = case.projection.results.get(result_id(ref))
                source = None if record is None else str(record.source_event_id)
            elif ref.startswith("evd_"):
                from yoetz.domain.values import evidence_id

                record = case.projection.evidence.get(evidence_id(ref))
                source = None if record is None else str(record.source_event_id)
            if source is None:
                return None
            resolved.add(source)
    if not resolved:
        return None
    return tuple(sorted(resolved, key=str.encode))


def _claims_unchanged_over_hidden_source(
    case: DeterministicCase,
    challenge: ReviewerChallenge,
) -> bool:
    """Whether this challenge asserts nothing changed while its own basis was withheld."""

    return any(
        "unchanged" in text.casefold()
        for text in (challenge.discrepancy, challenge.alternative_interpretation)
    ) and any(
        _UNAVAILABLE_GAPS & set(case.coverage_by_ref[cast(FindingBasisRef, ref)].known_gaps)
        for ref in challenge.cited_refs
        if ref in case.coverage_by_ref
    )


def validate_semantic_judgment(
    case: DeterministicCase,
    deterministic: tuple[Finding, ...],
    judgment: SemanticJudgment,
    provenance: SemanticProvenance,
    *,
    expected_frontier: Frontier,
) -> SemanticJudgmentReview:
    """Fence semantic challenges to the exact frozen refs, coverage, and final provenance.

    Each challenge is fenced independently against the same frozen case. A challenge that fails is
    dropped and counted by reason; the challenges beside it are unaffected, because nothing about
    one reviewer challenge is evidence about another. No fence is loosened here — the accept test
    for a single challenge is unchanged.

    The structural checks below stay hard failures: a wrong type, a drifted frontier, or a
    provenance that is not the final SUCCEEDED attempt means the *coordinator* handed this function
    the wrong inputs, not that the reviewer answered badly. They raise
    :class:`SemanticJudgmentRejected`, which the commit path converts into an honest
    ``invalid``/``semantic_judgment_rejected`` semantic outcome rather than losing the check.
    """

    if (
        type(case) is not DeterministicCase
        or type(judgment) is not SemanticJudgment
        or type(provenance) is not SemanticProvenance
        or expected_frontier != case.frontier
        or provenance.status is not SemanticStatus.SUCCEEDED
        or provenance.reason is not SemanticReason.SEMANTIC_COMPLETED
    ):
        raise _rejected("semantic_judgment_invalid")
    if judgment.conclusion != "challenges_returned":
        return SemanticJudgmentReview((), 0, ())
    coverage = case_coverage(case, semantic=True)
    candidates: list[CandidateFinding] = []
    rejections: dict[str, int] = {}
    for challenge in judgment.challenges:
        refs = _resolve_challenge_refs(case, deterministic, challenge)
        if refs is None:
            rejections[SEMANTIC_REJECTED_REF_OUTSIDE_CASE] = (
                rejections.get(SEMANTIC_REJECTED_REF_OUTSIDE_CASE, 0) + 1
            )
            continue
        if _claims_unchanged_over_hidden_source(case, challenge):
            rejections[SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM] = (
                rejections.get(SEMANTIC_REJECTED_HIDDEN_SOURCE_CLAIM, 0) + 1
            )
            continue
        policy_id, policy_version = _policy_identity(challenge.finding_kind)
        priority, _actionable = FINDING_KIND_TRAITS[challenge.finding_kind]
        candidates.append(
            CandidateFinding(
                challenge.finding_kind,
                FindingOrigin.SEMANTIC_MODEL_DERIVED,
                priority,
                challenge.summary,
                challenge.message_to_main_agent,
                tuple(
                    event_id(ref)
                    if ref.startswith("evt_")
                    else obligation_id(ref)
                    if ref.startswith("obl_")
                    else claim_id(ref)
                    for ref in refs
                ),
                policy_id,
                policy_version,
                case.frontier,
                coverage,
                provenance,
            )
        )
    return SemanticJudgmentReview(
        tuple(candidates),
        len(judgment.challenges),
        tuple(sorted(rejections.items(), key=lambda item: item[0].encode("ascii"))),
    )


def _request_digest(
    request: CheckRequest,
    scope: CheckScope,
    packs: tuple[str, ...],
    *,
    route_profile: Literal["policy", "strict"],
) -> str:
    source = cast(dict[str, JsonValue], request.model_dump(mode="json", by_alias=True))
    source["scope"] = {"claim_ids": scope.claim_ids, "obligation_ids": scope.obligation_ids}
    source["policy_packs"] = packs
    source["route_profile"] = route_profile
    return canonical_digest(source)


async def _semantic_evaluation(
    app: Application,
    request: CheckRequest,
    runtime: TaskRuntime,
    frozen: FrozenCase,
    deterministic: tuple[Finding, ...],
    *,
    route_profile: Literal["policy", "strict"],
    lineage_evaluation: LineageEvaluation | None = None,
) -> FinalSemanticEvaluation:
    if request.mode == "deterministic_only":
        return FinalSemanticEvaluation(
            SemanticStatus.NOT_REQUESTED,
            SemanticReason.DETERMINISTIC_MODE,
        )
    if route_profile == "strict":
        return FinalSemanticEvaluation(
            SemanticStatus.BLOCKED_BY_POLICY,
            SemanticReason.ROUTE_SEMANTIC_CEILING,
        )
    if RuntimeCapability.SEMANTIC not in runtime.capabilities:
        return FinalSemanticEvaluation(
            SemanticStatus.NOT_CONFIGURED,
            SemanticReason.PROVIDER_NOT_CONFIGURED,
        )
    try:
        if lineage_evaluation is None or not _semantic_evaluator_accepts_lineage(app):
            # Keep the original three-argument application seam for integrations that predate
            # the optional lineage semantic channel.  The production facade accepts the fourth
            # argument below; omitting it when there is no lineage also avoids turning an old
            # evaluator's harmless signature difference into a semantic coordinator failure.
            return await app.evaluate_semantic_check(frozen, deterministic, runtime)
        return await app.evaluate_semantic_check(frozen, deterministic, runtime, lineage_evaluation)
    except Exception as exc:
        # Optional/required semantic evaluator crash must never fabricate a clean semantic pass.
        record_unexpected_exception_without_raising(
            exc,
            component="check",
            operation="semantic_not_dispatched_coordinator_failure",
            request_id=request.request_id,
        )
        # Deliberately no operation_lease: reaching here means the evaluator raised *before* it
        # could renew, so the caller's token is still the live one. The evaluator itself catches
        # everything from the durable path and returns its renewed lease, and the attempt loop
        # terminalizes rather than raising — if either of those regresses, this path would start
        # handing back a stale lease and the check would be lost to OPERATION_PENDING instead of
        # recording an honest failure. There is no API to re-acquire a lease you already hold.
        return FinalSemanticEvaluation(
            SemanticStatus.FAILED,
            SemanticReason.COORDINATOR_FAILURE,
        )


def _semantic_evaluator_accepts_lineage(app: Application) -> bool:
    """Detect legacy application doubles before adding the optional lineage argument.

    The service facade owns the four-argument seam.  A few embedders still provide the original
    three-argument evaluator, so signature inspection keeps those integrations on their existing
    path without catching a ``TypeError`` raised from inside an evaluator as if it were a harmless
    signature mismatch.
    """

    try:
        parameters = tuple(inspect.signature(app.evaluate_semantic_check).parameters.values())
    except TypeError, ValueError:
        # An opaque callable may still accept the new optional argument; let the normal exception
        # fence below classify a genuine failure.
        return True
    return len(
        tuple(
            parameter
            for parameter in parameters
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
    ) >= 4 or any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters)


def _semantic_conclusion_token(result: FinalSemanticEvaluation) -> str:
    """The reviewer's own conclusion when it reached one; otherwise why it did not."""

    judgment = result.judgment
    if judgment is None:
        return result.reason.value
    return judgment.conclusion


def _record_semantic_review_accounting(
    request: CheckRequest,
    result: FinalSemanticEvaluation,
    review: SemanticJudgmentReview,
    semantic: tuple[Finding, ...],
    ranked: RankedFindings,
) -> None:
    """Say what the reviewer produced and what became of it, on every dispatched review.

    Without this, "the model returned three challenges and none of them reached you" is invisible:
    the check reports ``semantic_status: succeeded`` and zero semantic findings, which reads
    identically to a reviewer that found nothing. The record is counts and closed tokens only, and
    it reconciles — ``returned == accepted + rejected`` and ``accepted == selected + suppressed``.
    """

    # Provenance present means a provider attempt reached a terminal outcome. Nothing was reviewed
    # before that, so there is nothing to account for and no reason to fill the durable ring.
    if result.provenance is None:
        return
    selected = sum(
        1 for finding in ranked.findings if finding.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED
    )
    record_bounded_counts_without_raising(
        component="check",
        operation="semantic_review_accounting",
        outcome=result.status.value,
        request_id=request.request_id,
        counts={
            "semantic_conclusion": _semantic_conclusion_token(result),
            "semantic_challenges_returned": review.challenges_returned,
            "semantic_candidates_accepted": len(review.candidates),
            "semantic_challenges_rejected": review.challenges_rejected,
            "semantic_findings_selected": selected,
            "semantic_findings_suppressed": len(semantic) - selected,
        },
    )


def _judgment_rejected_evaluation(
    result: FinalSemanticEvaluation,
) -> FinalSemanticEvaluation:
    """Restate a structurally unusable reviewer answer as the honest terminal semantic outcome.

    ``SemanticStatus.INVALID`` / ``SEMANTIC_JUDGMENT_REJECTED`` has existed in the enum, the ledger
    CHECK constraints, and ``check-result-1.0.0`` since 0.1 and nothing had ever written it: the
    rejection escaped as ``INVALID_REQUEST`` instead, taking the whole check — deterministic
    findings included — with it. The provenance is the same attempt, restated to the outcome it
    actually reached, because the binding fence requires provenance and result to agree.
    """

    provenance = result.provenance
    if provenance is None:  # pragma: no cover - SUCCEEDED always carries final provenance
        return FinalSemanticEvaluation(SemanticStatus.FAILED, SemanticReason.COORDINATOR_FAILURE)
    return FinalSemanticEvaluation(
        SemanticStatus.INVALID,
        SemanticReason.SEMANTIC_JUDGMENT_REJECTED,
        None,
        # failure_class stays unset. Every member of the enum names a provider-side fault, and
        # this path runs only for the structural fence — the coordinator's own inputs were wrong,
        # not the provider's answer. Naming one would send an operator after the wrong component;
        # semantic_judgment_rejected already says exactly what happened.
        replace(
            provenance,
            status=SemanticStatus.INVALID,
            reason=SemanticReason.SEMANTIC_JUDGMENT_REJECTED,
        ),
        attempt_accounting=result.attempt_accounting,
        operation_lease=result.operation_lease,
        withheld_review_categories=result.withheld_review_categories,
        # The rejection restates the outcome, not the case: a truncated case stays truncated.
        case_content_over_item_limit=result.case_content_over_item_limit,
        case_reference_scope_reduced=result.case_reference_scope_reduced,
    )


async def execute_check_commit(
    app: Application,
    request: CheckRequest,
    *,
    route_profile: Literal["policy", "strict"] = "policy",
    host_profile: McpHostProfile = "generic",
    _state: Path | None = None,
) -> CheckCommitResult | CheckAwaitingHuman:
    """Freeze, evaluate, rank, and atomically commit one check operation.

    Returns ``CheckAwaitingHuman`` instead when the semantic phase is suspended on a local
    disclosure decision: nothing is committed and the operation stays resumable.

    ``_state`` isolates the applied-route drift probe (issue #537 slice C): production
    callers leave it unset so the probe reads the live state directory, while tests pass
    an isolated root.
    """

    if route_profile not in {"policy", "strict"}:
        raise TypeError("check_route_profile_invalid")
    scope = normalize_check_scope(request)
    packs = _selected_packs(request)
    required_capabilities = {
        RuntimeCapability.WRITE,
        RuntimeCapability.PAYLOAD_READ,
    }
    # A ready service grants SEMANTIC independently of the ordinary write route.  Preserve that
    # admission on non-deterministic checks; otherwise the leased task runtime loses the
    # capability and reports provider_not_configured before the configured evaluator can run.
    # An explicitly semantic request while semantic verification is disabled retains the existing
    # honest not-configured result instead of becoming a routing failure.
    if (
        route_profile == "policy"
        and request.mode != "deterministic_only"
        and app.verification_policy.semantic != "disabled"
    ):
        required_capabilities.add(RuntimeCapability.SEMANTIC)
    runtime = await app.runtime.route(
        RouteCommand(
            request.session_id,
            request.writer_id,
            RouteAccess.WRITE,
            frozenset(required_capabilities),
        )
    )
    frozen: FrozenCase | None = None
    lineage_evaluation: LineageEvaluation
    try:
        if runtime.session_id != request.session_id or runtime.writer_id != request.writer_id:
            raise PublicOperationError(
                PublicErrorCode.SESSION_CONFLICT,
                "The writer route is inconsistent.",
                False,
            )
        digest = _request_digest(request, scope, packs, route_profile=route_profile)
        try:
            frozen_or_replay = await runtime.ledger.freeze_case(
                request.session_id,
                request.writer_id,
                int(request.expected_frontier.sequence),
                request.request_id,
                digest,
            )
        except PublicOperationError as exc:
            # A completed same-request replay must return before consulting newer capture state:
            # a corrupt or unrelated ticket cannot turn an idempotent result into STORAGE_CORRUPT.
            # A new CHECK that hit the capture barrier gets one task-local authority reconciliation
            # and one retry; active tickets remain pending and every other error keeps its original
            # disposition.
            if exc.code is not PublicErrorCode.OPERATION_PENDING or not exc.retryable:
                raise
            reconcile_capture = getattr(app, "reconcile_observation_capture", None)
            if not callable(reconcile_capture):
                raise
            await cast(Callable[[TaskRuntime], Awaitable[None]], reconcile_capture)(runtime)
            frozen_or_replay = await runtime.ledger.freeze_case(
                request.session_id,
                request.writer_id,
                int(request.expected_frontier.sequence),
                request.request_id,
                digest,
            )
        if isinstance(frozen_or_replay, CheckCommitResult):
            replayed = await _attach_replayed_lineage_preview(runtime, frozen_or_replay)
            # The ledger replay is frozen; only the additive project-advice projection is current.
            return await _attach_current_project_advisory_notes(app, runtime.task_id, replayed)
        frozen = frozen_or_replay
        # The child preview and its coverage are evaluated from the same immutable parent prefix
        # that the check froze.  This is intentionally before semantic dispatch: a later child
        # sweep cannot alter this check's subject, and no child bundle is opened on this path.
        lineage_evaluation = await _lineage_at_frontier(
            runtime,
            frozen.case.frontier,
            base_coverage=case_coverage(frozen.case),
        )
        if frozen.lease.phase is CheckPhase.RESERVED:
            assessments, executions = run_deterministic_policies(frozen.case, scope, packs)
            assessments = assessments + await _coordination_assessments_for_frozen_case(
                app,
                runtime,
                frozen.case,
                scope,
                packs,
            )
            deterministic = allocate_findings(
                app.ids,
                tuple(item.candidate for item in assessments),
                prior_finding_ids(frozen.case.projection),
            )
            frozen = await _publish_deterministic_result(
                app,
                runtime,
                request,
                frozen,
                assessments,
                deterministic,
                executions,
                digest,
            )
        else:
            try:
                checkpoint = await _load_deterministic_result(
                    runtime,
                    request,
                    digest,
                    frozen,
                )
            except _DeterministicCheckpointSuperseded:
                # The checkpoint's bindings verified but its finding wording predates the
                # current text contract. The frozen case is unchanged and digest-verified, so
                # the deterministic phase recomputes from it instead of wedging the request
                # behind a non-retryable STORAGE_CORRUPT (issue #340). The stale checkpoint
                # keeps serving as the durable case pointer until commit clears it.
                record_bounded_counts_without_raising(
                    component="check",
                    operation="deterministic_checkpoint_superseded",
                    outcome="recomputed",
                    request_id=request.request_id,
                    counts={"superseded_checkpoints": 1},
                )
                assessments, executions = run_deterministic_policies(frozen.case, scope, packs)
                assessments = assessments + await _coordination_assessments_for_frozen_case(
                    app,
                    runtime,
                    frozen.case,
                    scope,
                    packs,
                )
                deterministic = allocate_findings(
                    app.ids,
                    tuple(item.candidate for item in assessments),
                    prior_finding_ids(frozen.case.projection),
                )
            else:
                deterministic = checkpoint.findings
                executions = checkpoint.executions
        semantic_wait = (
            request.mode != "deterministic_only"
            and RuntimeCapability.SEMANTIC in runtime.capabilities
        )
        if semantic_wait and frozen.lease.phase is CheckPhase.LOCAL_READY:
            lease = await runtime.ledger.advance_check_phase(
                frozen.lease,
                CheckPhase.LOCAL_READY,
                CheckPhase.SEMANTIC_WAIT,
            )
            frozen = FrozenCase(frozen.case, lease)
        semantic_result = await _semantic_evaluation(
            app,
            request,
            runtime,
            frozen,
            deterministic,
            route_profile=route_profile,
            lineage_evaluation=lineage_evaluation,
        )
        # Durable semantic attempts may renew the check lease (TTL 60s vs timeout up to 300s).
        if semantic_result.operation_lease is not None:
            frozen = FrozenCase(frozen.case, semantic_result.operation_lease)
        # A check waiting on a local disclosure decision returns here, before ranking, phase
        # advance, or commit. Everything below produces a terminal result, and a terminal result
        # is exactly what makes the human's later approval useless: the operation is closed, the
        # attempt is spent, and there is nothing left to resume. The operation stays in
        # SEMANTIC_WAIT so replaying this same request_id resumes the same request. A one-use
        # decision resumes its exact attempt; a standing-grant handoff has not created one yet.
        if (
            semantic_result.status is SemanticStatus.AWAITING_HUMAN
            and semantic_result.continuation is None
        ):
            # A suspension the caller cannot act on is not a suspension. The attempt runner
            # already terminalizes this case, so reaching here is a coordinator bug: degrade to
            # the ordinary terminal path rather than stranding the check on an unusable branch.
            record_bounded_counts_without_raising(
                component="check",
                operation="semantic_awaiting_human_without_continuation",
                outcome="internal_error",
                counts={"suspensions_without_continuation": 1},
                request_id=request.request_id,
            )
            semantic_result = replace(
                semantic_result,
                status=SemanticStatus.FAILED,
                reason=SemanticReason.COORDINATOR_FAILURE,
            )
        if semantic_result.status is SemanticStatus.AWAITING_HUMAN:
            assert semantic_result.continuation is not None
            if semantic_result.continuation.kind == REPOSITORY_GRANT_CONTINUATION_KIND:
                # No provider job or attempt exists for a missing standing grant. Expire this
                # operation lease before returning so exact same-request replay can reclaim it
                # immediately after the trusted ceremony (or reproduce the same handoff before
                # approval) without opening a fresh check.
                await runtime.ledger.suspend_check_for_repository_grant(frozen.lease)
            return CheckAwaitingHuman(
                runtime.task_id,
                request.session_id,
                request.writer_id,
                request.request_id,
                frozen.case.frontier,
                frozen.case.frontier,
                semantic_result.continuation,
                CheckVersionSlice("0.1", ENGINE_VERSION, PROJECTION_VERSION, packs),
            )
        review = _EMPTY_SEMANTIC_REVIEW
        if semantic_result.status is SemanticStatus.SUCCEEDED:
            assert semantic_result.judgment is not None
            assert semantic_result.provenance is not None
            try:
                review = validate_semantic_judgment(
                    frozen.case,
                    deterministic,
                    semantic_result.judgment,
                    semantic_result.provenance,
                    expected_frontier=frozen.case.frontier,
                )
            except SemanticJudgmentRejected as exc:
                # The reviewer's answer is unusable, so the check has no semantic result — but the
                # deterministic findings below were already earned and must still be committed.
                record_unexpected_exception_without_raising(
                    exc,
                    component="check",
                    operation="semantic_judgment_rejected",
                    request_id=request.request_id,
                )
                semantic_result = _judgment_rejected_evaluation(semantic_result)
        semantic = allocate_findings(app.ids, review.candidates)
        coverage = case_coverage(
            frozen.case,
            semantic=semantic_result.status is SemanticStatus.SUCCEEDED,
        )
        coverage = weakest(coverage, lineage_evaluation.coverage)
        policy_failed = any(item.outcome == "failed" for item in executions)
        semantic_failed = semantic_result.status not in {
            SemanticStatus.NOT_REQUESTED,
            SemanticStatus.SUCCEEDED,
        }
        semantic_gap = semantic_coverage_gap_code(semantic_result.status, semantic_result.reason)
        declared_gaps: set[str] = set() if semantic_gap is None else {semantic_gap}
        if (
            route_profile == "strict"
            and semantic_result.status is SemanticStatus.BLOCKED_BY_POLICY
            and semantic_result.reason is SemanticReason.ROUTE_SEMANTIC_CEILING
            and _strict_ceiling_route_drift(host_profile=host_profile, _state=_state)
        ):
            # The ceiling still blocks this process with the same status, reason, and null
            # provenance. The extra gap is the structural route_drift detail — applied policy
            # serving strict — and the receipt names the recovery; a genuinely applied
            # strict route keeps today's terminal wording exactly.
            declared_gaps.add(OPTIONAL_SEMANTIC_REVIEW_REGISTRATION_DRIFT_GAP)
        declared_gaps |= carried_semantic_attempt_gaps(frozen.case, semantic_result.status)
        # A review that ran without material its own profile selected is not full coverage, even
        # though it reports succeeded. Saying so here is what stops a hollow review from reading
        # as a clean one in the check result and the receipt derived from it.
        if (
            semantic_result.status is SemanticStatus.SUCCEEDED
            and semantic_result.withheld_review_categories
        ):
            declared_gaps.add(SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP)
        # A challenge the fence dropped is material the reviewer raised and the check does not
        # carry. Saying so is what keeps a dropped challenge from reading as one never made.
        if review.challenges_rejected:
            declared_gaps.add(SEMANTIC_CHALLENGES_REJECTED_GAP)
        # Recorded prose the case could not carry whole. The reviewer answered on a fragment, and
        # the author has no other signal that the text they published never arrived (issue #177).
        if semantic_result.case_content_over_item_limit:
            declared_gaps.add(SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP)
        if semantic_result.case_reference_scope_reduced:
            declared_gaps.add("semantic_reference_scope_reduced")
        new_gaps = declared_gaps - set(coverage.known_gaps)
        if new_gaps:
            gaps = set(coverage.known_gaps) | new_gaps
            freshness = coverage.ledger_freshness
            if freshness is LedgerFreshness.CURRENT:
                freshness = LedgerFreshness.PARTIAL
            coverage = replace(
                coverage,
                ledger_freshness=freshness,
                known_gaps=tuple(sorted(gaps, key=str.encode)),
            )
        completion_scope_incomplete = bool(
            {COMPLETION_SCOPE_DECLARED_NONE_GAP, COMPLETION_SCOPE_UNDECLARED_GAP}
            & set(coverage.known_gaps)
        )
        if completion_scope_incomplete:
            # Completion scope is the subject-level boundary named by ADR-019. Even a separately
            # incomplete required semantic attempt cannot turn either closed scope gap into the
            # broader incomplete-check verdict.
            completeness = CheckCompleteness.COVERAGE_INCOMPLETE
        elif policy_failed or (request.mode == "semantic_required" and semantic_failed):
            completeness = CheckCompleteness.REQUIRED_INCOMPLETE
        elif coverage.known_gaps or semantic_failed:
            completeness = CheckCompleteness.COVERAGE_INCOMPLETE
        else:
            completeness = CheckCompleteness.COMPLETE
        maximum = (
            app.verification_policy.max_findings
            if request.max_findings is None
            else int(request.max_findings)
        )
        ranked = rank_findings(
            deterministic,
            semantic,
            RankingContext(coverage, completeness),
            maximum,
        )
        _record_semantic_review_accounting(
            request,
            semantic_result,
            review,
            semantic,
            ranked,
        )
        if frozen.lease.phase is not CheckPhase.READY_TO_FINALIZE:
            lease = await runtime.ledger.advance_check_phase(
                frozen.lease,
                CheckPhase.SEMANTIC_WAIT if semantic_wait else CheckPhase.LOCAL_READY,
                CheckPhase.READY_TO_FINALIZE,
            )
            frozen = FrozenCase(frozen.case, lease)
        committed = await runtime.ledger.commit_check_if_current(
            frozen,
            ranked,
            executions,
            semantic_result.status,
            semantic_result.reason,
            semantic_result.provenance,
            request.request_id,
            scope=CheckScopeModel(claim_ids=scope.claim_ids, obligation_ids=scope.obligation_ids),
        )
        preview = _lineage_preview(lineage_evaluation, frozen.case.frontier)
        projected = committed if preview is None else replace(committed, children=preview)
        # Advice is deliberately attached after the deterministic commit.  It is current
        # projection context, never part of the frozen check event or its verdict calculation.
        return await _attach_current_project_advisory_notes(app, runtime.task_id, projected)
    except PublicOperationError as exc:
        if frozen is not None and not exc.retryable:
            try:
                await runtime.ledger.fail_check_if_current(frozen.lease, exc)
            except Exception as terminalize_exc:
                record_unexpected_exception_without_raising(
                    terminalize_exc,
                    component="check",
                    operation="check_pipeline_terminalization_failed",
                    request_id=request.request_id,
                )
        raise
    except Exception as exc:
        # The request is already a validated CheckRequest. Any protocol-value failure raised from
        # this pipeline is therefore an implementation/storage defect, not malformed caller input.
        # Close an admitted operation before surfacing the internal error so exact replay and
        # status never wait forever on work this invocation has abandoned.
        record_unexpected_exception_without_raising(
            exc,
            component="check",
            operation="check_pipeline_failed",
            request_id=request.request_id,
        )
        failure = PublicOperationError(
            PublicErrorCode.INTERNAL_ERROR,
            "The check failed internally.",
            False,
        )
        if frozen is not None:
            try:
                await runtime.ledger.fail_check_if_current(frozen.lease, failure)
            except Exception as terminalize_exc:
                record_unexpected_exception_without_raising(
                    terminalize_exc,
                    component="check",
                    operation="check_pipeline_terminalization_failed",
                    request_id=request.request_id,
                )
        raise failure from exc
    finally:
        await app.runtime.release(runtime)


async def execute_check(
    app: Application,
    request: CheckRequest,
    *,
    route_profile: Literal["policy", "strict"] = "policy",
    host_profile: McpHostProfile = "generic",
    _state: Path | None = None,
) -> CheckCommitResult | CheckAwaitingHuman:
    """Return the closed sink-independent result for the facade's sole projection step."""

    # Omitted mode resolves via policy so recorded check events always carry a concrete mode.
    if request.mode is None:
        request = request.model_copy(update={"mode": app.verification_policy.default_check_mode})
    return await execute_check_commit(
        app,
        request,
        route_profile=route_profile,
        host_profile=host_profile,
        _state=_state,
    )
