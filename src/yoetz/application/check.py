"""Deterministic check coordination and semantic-result validation fences."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CandidateFinding,
    Finding,
    FindingKind,
    FindingOrigin,
    SemanticProvenance,
    finding_from_json,
    finding_to_json,
    semantic_provenance_to_json,
)
from yoetz.domain.receipts import (
    OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
    SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
    SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
)
from yoetz.domain.values import (
    Frontier,
    claim_id,
    event_id,
    finding_id,
    freeze_json,
    obligation_id,
)
from yoetz.kernel.deterministic_checks import (
    DeterministicAssessment,
    DeterministicCase,
    FindingBasisRef,
    finding_basis_from_json,
    finding_basis_to_json,
)
from yoetz.kernel.policies.research_evidence import research_evidence_findings
from yoetz.kernel.policies.work_integrity import work_integrity_findings
from yoetz.kernel.ranking import CheckCompleteness, RankingContext, rank_findings
from yoetz.ports.clock import ClockPort
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    CheckCommitResult,
    CheckPhase,
    CheckPolicyExecution,
    FrozenCase,
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
    CheckType,
    Coverage,
    LedgerFreshness,
    PublicationChannel,
    coverage_for_channel,
    coverage_to_json,
    weakest,
)
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import (
    CheckRequest,
    SemanticReason,
    SemanticStatus,
    validate_semantic_outcome,
    validate_semantic_provenance_binding,
)

__all__ = [
    "Application",
    "CheckScope",
    "FinalSemanticEvaluation",
    "allocate_findings",
    "case_coverage",
    "check_internal_json",
    "execute_check",
    "execute_check_commit",
    "normalize_check_scope",
    "run_deterministic_policies",
    "semantic_coverage_gap_code",
    "validate_semantic_judgment",
]

_RESEARCH_PACK = "research-evidence/0.1.0"
_WORK_PACK = "work-integrity/0.1.0"
_CANONICAL_PACKS = (_RESEARCH_PACK, _WORK_PACK)
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


def _invalid(reason: str = "check_coordinator_invalid") -> ValueError:
    return ValueError(reason)


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


def check_internal_json(result: CheckCommitResult) -> dict[str, JsonValue]:
    """Serialize sink-independent CHECK success without a privacy projection."""

    return {
        "protocol_version": "0.1",
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
    }


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


def semantic_coverage_gap_code(status: SemanticStatus, reason: SemanticReason) -> str | None:
    """Map a terminal semantic outcome to the receipt/check structural gap code, or None."""

    validate_semantic_outcome(status, reason)
    if status is SemanticStatus.SUCCEEDED:
        return None
    if status is SemanticStatus.NOT_REQUESTED:
        return SEMANTIC_REVIEW_NOT_REQUESTED_GAP
    if status is SemanticStatus.BLOCKED_BY_POLICY:
        return OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP
    if status is SemanticStatus.NOT_CONFIGURED:
        return SEMANTIC_REVIEW_NOT_CONFIGURED_GAP
    return SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP


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

    async def evaluate_semantic_check(
        self,
        frozen: FrozenCase,
        deterministic_findings: tuple[Finding, ...],
    ) -> FinalSemanticEvaluation: ...


type _PolicyEvaluator = Callable[[DeterministicCase], tuple[DeterministicAssessment, ...]]


@dataclass(frozen=True, slots=True)
class _DurableDeterministicResult:
    findings: tuple[Finding, ...]
    executions: tuple[CheckPolicyExecution, ...]


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
        if frozenset(source) != frozenset(
            {
                "schema_version",
                "request_id",
                "request_digest",
                "task_id",
                "session_id",
                "writer_id",
                "subject_frontier",
                "dependency_digest",
                "prior_resume",
                "policy_executions",
                "assessments",
            }
        ):
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
            DeterministicAssessment(candidate, basis)
            findings.append(finding)
        if len({item.finding_id for item in findings}) != len(findings):
            raise ValueError("deterministic_result_finding_invalid")
        return _DurableDeterministicResult(tuple(findings), executions)
    except PublicOperationError:
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
    else:
        collections = (
            case.projection.obligations,
            case.projection.claims,
            case.projection.actions,
            case.projection.results,
            case.projection.evidence,
            case.projection.findings,
            case.projection.responses,
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
    assessments = by_pack.get(_WORK_PACK, ()) + by_pack.get(_RESEARCH_PACK, ())
    keys = tuple(
        (item.candidate.policy_id, item.basis.rule_id, item.candidate.subject_refs)
        for item in assessments
    )
    if len(keys) != len(set(keys)):
        raise _invalid("duplicate_deterministic_assessment")
    return assessments, tuple(executions)


def allocate_findings(ids: IdPort, candidates: tuple[CandidateFinding, ...]) -> tuple[Finding, ...]:
    """Allocate stable IDs in deterministic candidate order."""

    output: list[Finding] = []
    for candidate in candidates:
        if type(candidate) is not CandidateFinding:
            raise _invalid("finding_candidate_invalid")
        output.append(
            Finding(
                finding_id(ids.new(IdKind.FINDING)),
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


def case_coverage(case: DeterministicCase, *, semantic: bool = False) -> Coverage:
    """Fold every frozen material dependency and explicit case gap conservatively."""

    ordered = tuple(case.coverage_by_ref[key] for key in sorted(case.coverage_by_ref, key=str))
    if ordered:
        result = ordered[0]
        for coverage in ordered[1:]:
            result = weakest(result, coverage)
    else:
        result = coverage_for_channel(PublicationChannel.ENGINE_DERIVED)
    gaps = set(result.known_gaps)
    gaps.update(gap.code for gap in case.gaps)
    channels = set(result.publication_channels)
    channels.add(PublicationChannel.ENGINE_DERIVED)
    checks = set(result.check_types)
    checks.discard(CheckType.NONE)
    checks.add(CheckType.DETERMINISTIC)
    if semantic:
        checks.add(CheckType.SEMANTIC_MODEL_DERIVED)
    freshness = result.ledger_freshness
    if gaps and freshness is LedgerFreshness.CURRENT:
        freshness = LedgerFreshness.PARTIAL
    return replace(
        result,
        publication_channels=tuple(sorted(channels, key=lambda value: value.value.encode("ascii"))),
        ledger_freshness=freshness,
        check_types=tuple(sorted(checks, key=lambda value: value.value.encode("ascii"))),
        known_gaps=tuple(sorted(gaps, key=str.encode)),
    )


def _policy_identity(kind: FindingKind) -> tuple[str, str]:
    return (
        ("work-integrity", "0.1.0")
        if kind in _WORK_KINDS
        else (
            "research-evidence",
            "0.1.0",
        )
    )


def _resolve_challenge_refs(
    case: DeterministicCase,
    deterministic: tuple[Finding, ...],
    challenge: ReviewerChallenge,
) -> tuple[str, ...]:
    findings = {str(item.finding_id): item for item in deterministic}
    resolved: set[str] = set()
    for ref in challenge.cited_refs:
        if ref.startswith("fnd_"):
            finding = findings.get(ref)
            if finding is None:
                raise _invalid("semantic_ref_outside_case")
            resolved.update(map(str, finding.subject_refs))
        elif ref not in case.allowed_ids:
            raise _invalid("semantic_ref_outside_case")
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
                raise _invalid("semantic_ref_outside_case")
            resolved.add(source)
    if not resolved:
        raise _invalid("semantic_ref_outside_case")
    return tuple(sorted(resolved, key=str.encode))


def validate_semantic_judgment(
    case: DeterministicCase,
    deterministic: tuple[Finding, ...],
    judgment: SemanticJudgment,
    provenance: SemanticProvenance,
    *,
    expected_frontier: Frontier,
) -> tuple[CandidateFinding, ...]:
    """Fence semantic challenges to the exact frozen refs, coverage, and final provenance."""

    if (
        type(case) is not DeterministicCase
        or type(judgment) is not SemanticJudgment
        or type(provenance) is not SemanticProvenance
        or expected_frontier != case.frontier
        or provenance.status is not SemanticStatus.SUCCEEDED
        or provenance.reason is not SemanticReason.SEMANTIC_COMPLETED
    ):
        raise _invalid("semantic_judgment_invalid")
    if judgment.conclusion != "challenges_returned":
        return ()
    coverage = case_coverage(case, semantic=True)
    candidates: list[CandidateFinding] = []
    for challenge in judgment.challenges:
        refs = _resolve_challenge_refs(case, deterministic, challenge)
        if any(
            "unchanged" in text.casefold()
            for text in (challenge.discrepancy, challenge.alternative_interpretation)
        ) and any(
            _UNAVAILABLE_GAPS & set(case.coverage_by_ref[cast(FindingBasisRef, ref)].known_gaps)
            for ref in challenge.cited_refs
            if ref in case.coverage_by_ref
        ):
            raise _invalid("semantic_hidden_source_claim")
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
    return tuple(candidates)


def _request_digest(request: CheckRequest, scope: CheckScope, packs: tuple[str, ...]) -> str:
    source = cast(dict[str, JsonValue], request.model_dump(mode="json", by_alias=True))
    source["scope"] = {"claim_ids": scope.claim_ids, "obligation_ids": scope.obligation_ids}
    source["policy_packs"] = packs
    return canonical_digest(source)


async def _semantic_evaluation(
    app: Application,
    request: CheckRequest,
    runtime: TaskRuntime,
    frozen: FrozenCase,
    deterministic: tuple[Finding, ...],
) -> FinalSemanticEvaluation:
    if request.mode == "deterministic_only":
        return FinalSemanticEvaluation(
            SemanticStatus.NOT_REQUESTED,
            SemanticReason.DETERMINISTIC_MODE,
        )
    if RuntimeCapability.SEMANTIC not in runtime.capabilities:
        return FinalSemanticEvaluation(
            SemanticStatus.NOT_CONFIGURED,
            SemanticReason.PROVIDER_NOT_CONFIGURED,
        )
    try:
        return await app.evaluate_semantic_check(frozen, deterministic)
    except Exception:
        # Optional/required semantic evaluator crash must never fabricate a clean semantic pass.
        return FinalSemanticEvaluation(
            SemanticStatus.FAILED,
            SemanticReason.COORDINATOR_FAILURE,
        )


async def execute_check_commit(app: Application, request: CheckRequest) -> CheckCommitResult:
    """Freeze, evaluate, rank, and atomically commit one check operation."""

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
    if request.mode != "deterministic_only" and app.verification_policy.semantic != "disabled":
        required_capabilities.add(RuntimeCapability.SEMANTIC)
    runtime = await app.runtime.route(
        RouteCommand(
            request.session_id,
            request.writer_id,
            RouteAccess.WRITE,
            frozenset(required_capabilities),
        )
    )
    try:
        if runtime.session_id != request.session_id or runtime.writer_id != request.writer_id:
            raise PublicOperationError(
                PublicErrorCode.SESSION_CONFLICT,
                "The writer route is inconsistent.",
                False,
            )
        digest = _request_digest(request, scope, packs)
        frozen_or_replay = await runtime.ledger.freeze_case(
            request.session_id,
            request.writer_id,
            int(request.expected_frontier.sequence),
            request.request_id,
            digest,
        )
        if isinstance(frozen_or_replay, CheckCommitResult):
            return frozen_or_replay
        frozen = frozen_or_replay
        if frozen.lease.phase is CheckPhase.RESERVED:
            assessments, executions = run_deterministic_policies(frozen.case, scope, packs)
            deterministic = allocate_findings(
                app.ids,
                tuple(item.candidate for item in assessments),
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
            checkpoint = await _load_deterministic_result(
                runtime,
                request,
                digest,
                frozen,
            )
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
        semantic_result = await _semantic_evaluation(app, request, runtime, frozen, deterministic)
        semantic_candidates: tuple[CandidateFinding, ...] = ()
        if semantic_result.status is SemanticStatus.SUCCEEDED:
            assert semantic_result.judgment is not None
            assert semantic_result.provenance is not None
            semantic_candidates = validate_semantic_judgment(
                frozen.case,
                deterministic,
                semantic_result.judgment,
                semantic_result.provenance,
                expected_frontier=frozen.case.frontier,
            )
        semantic = allocate_findings(app.ids, semantic_candidates)
        coverage = case_coverage(
            frozen.case,
            semantic=semantic_result.status is SemanticStatus.SUCCEEDED,
        )
        policy_failed = any(item.outcome == "failed" for item in executions)
        semantic_failed = semantic_result.status not in {
            SemanticStatus.NOT_REQUESTED,
            SemanticStatus.SUCCEEDED,
        }
        semantic_gap = semantic_coverage_gap_code(semantic_result.status, semantic_result.reason)
        if semantic_gap is not None and semantic_gap not in coverage.known_gaps:
            gaps = set(coverage.known_gaps)
            gaps.add(semantic_gap)
            freshness = coverage.ledger_freshness
            if freshness is LedgerFreshness.CURRENT:
                freshness = LedgerFreshness.PARTIAL
            coverage = replace(
                coverage,
                ledger_freshness=freshness,
                known_gaps=tuple(sorted(gaps, key=str.encode)),
            )
        if policy_failed or (request.mode == "semantic_required" and semantic_failed):
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
        if frozen.lease.phase is not CheckPhase.READY_TO_FINALIZE:
            lease = await runtime.ledger.advance_check_phase(
                frozen.lease,
                CheckPhase.SEMANTIC_WAIT if semantic_wait else CheckPhase.LOCAL_READY,
                CheckPhase.READY_TO_FINALIZE,
            )
            frozen = FrozenCase(frozen.case, lease)
        return await runtime.ledger.commit_check_if_current(
            frozen,
            ranked,
            executions,
            semantic_result.status,
            semantic_result.reason,
            semantic_result.provenance,
            request.request_id,
        )
    except ProtocolValueError as exc:
        raise PublicOperationError(
            PublicErrorCode.INVALID_REQUEST,
            "The check request is invalid.",
            False,
        ) from exc
    finally:
        await app.runtime.release(runtime)


async def execute_check(app: Application, request: CheckRequest) -> CheckCommitResult:
    """Return the closed sink-independent result for the facade's sole projection step."""

    # Omitted mode resolves via policy so recorded check events always carry a concrete mode.
    if request.mode is None:
        request = request.model_copy(update={"mode": app.verification_policy.default_check_mode})
    return await execute_check_commit(app, request)
