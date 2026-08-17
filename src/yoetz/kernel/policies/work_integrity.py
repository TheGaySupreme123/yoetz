"""Deterministic work-integrity policy pack."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, cast

from yoetz.domain.events import (
    ClaimKind,
    ObligationChangeKind,
    ObligationStatus,
    ResponseDisposition,
    ResultOutcome,
)
from yoetz.domain.findings import FindingKind
from yoetz.domain.values import (
    EvidenceId,
    ObligationId,
    ResultId,
    SubjectStateRef,
    SubjectStateRelation,
    subject_state_relation,
)
from yoetz.kernel.deterministic_checks import (
    DeterministicAssessment,
    DeterministicCase,
    FindingBasisRef,
    FindingFact,
    FrozenSourceAvailability,
    PolicyPack,
    build_policy_assessment,
    policy_public_root,
    policy_source_availability,
)
from yoetz.kernel.plan_scope import current_plan_scope
from yoetz.kernel.policies.response_support import (
    BASE_RESPONSE_INADMISSIBLE_GAPS,
    WORK_RESPONSE_PRESENT_FACT,
    response_support_admissible,
)

__all__ = [
    "WORK_INTEGRITY_FACT_CODES",
    "WORK_INTEGRITY_POLICY_ID",
    "WORK_INTEGRITY_POLICY_PACK",
    "WORK_INTEGRITY_POLICY_VERSION",
    "work_integrity_findings",
]

WORK_INTEGRITY_POLICY_ID: Final = "work-integrity"
WORK_INTEGRITY_POLICY_VERSION: Final = "0.1.0"
WORK_INTEGRITY_POLICY_PACK: Final = PolicyPack(
    WORK_INTEGRITY_POLICY_ID,
    WORK_INTEGRITY_POLICY_VERSION,
)
WORK_INTEGRITY_FACT_CODES: Final = frozenset(
    {
        "completion_claim_present",
        "open_obligation_present",
        "valid_waiver_absent",
        "requested_item_present",
        "linked_attempt_absent",
        "failed_result_present",
        "failure_disclosure_absent",
        "claim_present",
        "admissible_evidence_absent",
        "result_present",
        "linked_action_absent",
        "action_present",
        "linked_result_absent",
        "subsequent_unrelated_work_present",
        "state_comparison_available",
        "state_changed",
        "evidence_state_mismatch",
        "contradictory_claims_present",
        "resolution_absent",
        "unknown_event_present",
        "redaction_gap_present",
        "freshness_gap_present",
        "finding_response_present",
        "response_basis_insufficient",
        "response_state_stale",
    }
)

_RULE_ORDER: Final = (
    FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
    FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED,
    FindingKind.FAILED_WORK_OMITTED,
    FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
    FindingKind.RESULT_WITHOUT_ACTION,
    FindingKind.ACTION_WITHOUT_RESULT,
    FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
    FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED,
    FindingKind.LEDGER_STALE_OR_INCOMPLETE,
    FindingKind.WEAK_OR_STALE_RESPONSE,
)
_WORK_RESPONSE_INADMISSIBLE_GAPS: Final = BASE_RESPONSE_INADMISSIBLE_GAPS | {
    "evidence_digest_subject_legacy_unknown"
}


def _ascii(value: str) -> bytes:
    return value.encode("ascii", errors="strict")


def _refs(values: Iterable[FindingBasisRef]) -> tuple[FindingBasisRef, ...]:
    return tuple(sorted(set(values), key=_ascii))


def _fact(code: str, *refs: FindingBasisRef) -> FindingFact:
    return FindingFact(code, _refs(refs))


def _coverage_admissible(case: DeterministicCase, ref: FindingBasisRef) -> bool:
    coverage = case.coverage_by_ref.get(ref)
    if coverage is None:
        return False
    return not _WORK_RESPONSE_INADMISSIBLE_GAPS & set(coverage.known_gaps)


def _claim_support_is_admissible(
    case: DeterministicCase,
    claim_kind: ClaimKind,
    claim_state: SubjectStateRef | None,
    ref: FindingBasisRef,
) -> bool:
    if ref not in case.allowed_ids or not _coverage_admissible(case, ref):
        return False
    if ref.startswith("evd_"):
        record = case.projection.evidence.get(EvidenceId(ref))
        if record is None or record.payload is None:
            return False
        return (
            subject_state_relation(record.payload.subject_state, claim_state)
            is not SubjectStateRelation.DIFFERENT
        )
    if ref.startswith("res_"):
        record = case.projection.results.get(ResultId(ref))
        if record is None or record.payload is None:
            return False
        if (
            claim_kind is ClaimKind.COMPLETION
            and record.payload.outcome is not ResultOutcome.SUCCESS
        ):
            return False
        return (
            subject_state_relation(record.payload.subject_state, claim_state)
            is not SubjectStateRelation.DIFFERENT
        )
    if ref.startswith("obl_"):
        record = case.projection.obligations.get(ObligationId(ref))
        if record is None or record.payload is None:
            return False
        return (
            claim_kind is not ClaimKind.COMPLETION
            or record.payload.status is ObligationStatus.RESOLVED
            or record.plan_change is ObligationChangeKind.WAIVED
        )
    return False


def _active_requested_obligations(case: DeterministicCase) -> frozenset[ObligationId]:
    scope = current_plan_scope(case.projection.plans, case.projection.coverage_gaps)
    if scope.effective_obligation_refs is None:
        # The frozen case already carries the plan redaction/unknown-event gap. Do not invent a
        # partial obligation set from whichever readable plan fragments happen to remain.
        return frozenset()
    return frozenset(
        obligation
        for obligation in scope.effective_obligation_refs
        if (record := case.projection.obligations.get(obligation)) is not None
        and record.payload is not None
    )


def _action_subject_key(
    obligation_refs: tuple[str, ...],
    attempted_items: tuple[str, ...],
) -> tuple[str, frozenset[str]] | None:
    if obligation_refs:
        return ("obligations", frozenset(obligation_refs))
    if attempted_items:
        return ("requested_items", frozenset(attempted_items))
    return None


def _keys_are_disjoint(
    left: tuple[str, frozenset[str]] | None,
    right: tuple[str, frozenset[str]] | None,
) -> bool:
    return (
        left is not None
        and right is not None
        and left[0] == right[0]
        and left[1].isdisjoint(right[1])
    )


def _response_support_admissible(
    case: DeterministicCase,
    refs: tuple[EvidenceId | ResultId, ...],
) -> bool:
    return response_support_admissible(
        case,
        refs,
        inadmissible_gaps=_WORK_RESPONSE_INADMISSIBLE_GAPS,
    )


def _completion_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for claim_id, claim_record in sorted(
        case.projection.claims.items(), key=lambda item: _ascii(item[0])
    ):
        claim = claim_record.payload
        if claim is None or claim.claim_kind is not ClaimKind.COMPLETION:
            continue
        for obligation_ref in claim.obligation_refs:
            obligation_record = case.projection.obligations.get(obligation_ref)
            if obligation_record is None or obligation_record.payload is None:
                continue
            if (
                obligation_record.payload.status is not ObligationStatus.OPEN
                or obligation_record.plan_change is ObligationChangeKind.WAIVED
            ):
                continue
            subjects = _refs((claim_id, obligation_ref))
            output.append(
                build_policy_assessment(
                    case,
                    WORK_INTEGRITY_POLICY_PACK,
                    FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
                    subjects,
                    (
                        _fact("completion_claim_present", claim_id),
                        _fact("open_obligation_present", obligation_ref),
                    ),
                    (_fact("valid_waiver_absent", claim_id, obligation_ref),),
                )
            )
    return output


def _requested_item_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    attempted = {
        item
        for record in case.projection.actions.values()
        if record.payload is not None
        for item in record.payload.attempted_items
    }
    output: list[DeterministicAssessment] = []
    for obligation_ref in sorted(_active_requested_obligations(case), key=_ascii):
        record = case.projection.obligations[obligation_ref]
        payload = record.payload
        if payload is None or not any(
            item.value not in attempted for item in payload.requested_items
        ):
            continue
        output.append(
            build_policy_assessment(
                case,
                WORK_INTEGRITY_POLICY_PACK,
                FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED,
                (obligation_ref,),
                (_fact("requested_item_present", obligation_ref),),
                (_fact("linked_attempt_absent", obligation_ref),),
            )
        )
    return output


def _failed_work_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    failures = tuple(
        result_id
        for result_id, record in case.projection.results.items()
        if record.payload is not None
        and record.payload.outcome in {ResultOutcome.FAILURE, ResultOutcome.PARTIAL}
    )
    for claim_id, claim_record in case.projection.claims.items():
        claim = claim_record.payload
        if claim is None or claim.claim_kind is not ClaimKind.COMPLETION:
            continue
        for result_id in failures:
            if result_id in claim.supporting_refs:
                continue
            output.append(
                build_policy_assessment(
                    case,
                    WORK_INTEGRITY_POLICY_PACK,
                    FindingKind.FAILED_WORK_OMITTED,
                    _refs((claim_id, policy_public_root(case, result_id))),
                    (_fact("failed_result_present", result_id),),
                    (_fact("failure_disclosure_absent", claim_id, result_id),),
                    source_availability=policy_source_availability(case, (result_id,)),
                )
            )
    return output


def _unsupported_claim_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for claim_id, record in case.projection.claims.items():
        claim = record.payload
        if claim is None:
            continue
        support_refs = cast(tuple[FindingBasisRef, ...], claim.supporting_refs)
        if any(
            _claim_support_is_admissible(
                case,
                claim.claim_kind,
                claim.subject_state,
                ref,
            )
            for ref in support_refs
        ):
            continue
        availability = (
            FrozenSourceAvailability.NOT_RECORDED
            if not support_refs
            else policy_source_availability(case, support_refs)
        )
        output.append(
            build_policy_assessment(
                case,
                WORK_INTEGRITY_POLICY_PACK,
                FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
                (claim_id,),
                (_fact("claim_present", claim_id),),
                (_fact("admissible_evidence_absent", claim_id),),
                source_availability=availability,
            )
        )
    return output


def _orphan_result_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for result_id, record in case.projection.results.items():
        result = record.payload
        if result is None:
            continue
        action = case.projection.actions.get(result.action_id)
        if action is not None and action.payload is not None:
            continue
        output.append(
            build_policy_assessment(
                case,
                WORK_INTEGRITY_POLICY_PACK,
                FindingKind.RESULT_WITHOUT_ACTION,
                (policy_public_root(case, result_id),),
                (_fact("result_present", result_id),),
                (_fact("linked_action_absent", result_id),),
            )
        )
    return output


def _unresolved_action_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    linked_actions = {
        record.payload.action_id
        for record in case.projection.results.values()
        if record.payload is not None
    }
    actions = tuple(
        sorted(
            (
                (action_id, record)
                for action_id, record in case.projection.actions.items()
                if record.payload is not None
            ),
            key=lambda item: (item[1].source_frontier, _ascii(item[0])),
        )
    )
    output: list[DeterministicAssessment] = []
    for action_id, record in actions:
        action = record.payload
        if action is None or action_id in linked_actions:
            continue
        key = _action_subject_key(action.obligation_refs, action.attempted_items)
        later = tuple(
            later_id
            for later_id, later_record in actions
            if later_record.source_frontier > record.source_frontier
            and later_record.payload is not None
            and _keys_are_disjoint(
                key,
                _action_subject_key(
                    later_record.payload.obligation_refs,
                    later_record.payload.attempted_items,
                ),
            )
        )
        if not later:
            continue
        output.append(
            build_policy_assessment(
                case,
                WORK_INTEGRITY_POLICY_PACK,
                FindingKind.ACTION_WITHOUT_RESULT,
                (policy_public_root(case, action_id),),
                (
                    _fact("action_present", action_id),
                    _fact("subsequent_unrelated_work_present", action_id, *later),
                ),
                (_fact("linked_result_absent", action_id),),
            )
        )
    return output


def _state_pairs(
    case: DeterministicCase,
) -> tuple[tuple[EvidenceId, FindingBasisRef, SubjectStateRef, SubjectStateRef], ...]:
    pairs: set[tuple[EvidenceId, FindingBasisRef, SubjectStateRef, SubjectStateRef]] = set()
    for claim_id, record in case.projection.claims.items():
        claim = record.payload
        if claim is None or claim.subject_state is None:
            continue
        for support in claim.supporting_refs:
            if support.startswith("evd_"):
                evidence = case.projection.evidence.get(EvidenceId(support))
                if (
                    evidence is not None
                    and evidence.payload is not None
                    and evidence.payload.subject_state is not None
                ):
                    pairs.add(
                        (
                            EvidenceId(support),
                            claim_id,
                            evidence.payload.subject_state,
                            claim.subject_state,
                        )
                    )
    for result_id, record in case.projection.results.items():
        result = record.payload
        if result is None or result.subject_state is None:
            continue
        for evidence_id in result.evidence_refs:
            evidence = case.projection.evidence.get(evidence_id)
            if (
                evidence is not None
                and evidence.payload is not None
                and evidence.payload.subject_state is not None
            ):
                pairs.add(
                    (evidence_id, result_id, evidence.payload.subject_state, result.subject_state)
                )
    return tuple(sorted(pairs, key=lambda item: (_ascii(item[0]), _ascii(item[1]))))


def _stale_evidence_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for evidence_id, checked_ref, evidence_state, checked_state in _state_pairs(case):
        relation = subject_state_relation(evidence_state, checked_state)
        if relation is not SubjectStateRelation.DIFFERENT:
            continue
        fact_refs = _refs((evidence_id, checked_ref))
        output.append(
            build_policy_assessment(
                case,
                WORK_INTEGRITY_POLICY_PACK,
                FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
                _refs(
                    (
                        policy_public_root(case, evidence_id),
                        policy_public_root(case, checked_ref),
                    )
                ),
                (
                    FindingFact("state_comparison_available", fact_refs),
                    FindingFact("state_changed", fact_refs),
                    FindingFact("evidence_state_mismatch", fact_refs),
                ),
                subject_state_relation=relation,
                source_availability=policy_source_availability(case, fact_refs),
            )
        )
    return output


def _contradiction_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for key in sorted(
        case.projection.contradictions,
        key=lambda item: (_ascii(item.disputing_claim_id), _ascii(item.disputed_ref)),
    ):
        refs = _refs((key.disputing_claim_id, key.disputed_ref))
        if any(ref not in case.allowed_ids for ref in refs):
            continue
        output.append(
            build_policy_assessment(
                case,
                WORK_INTEGRITY_POLICY_PACK,
                FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED,
                refs,
                (FindingFact("contradictory_claims_present", refs),),
                (FindingFact("resolution_absent", refs),),
            )
        )
    return output


def _ledger_finding(case: DeterministicCase) -> list[DeterministicAssessment]:
    classes: dict[str, set[FindingBasisRef]] = {
        "unknown_event_present": set(),
        "redaction_gap_present": set(),
        "freshness_gap_present": set(),
    }
    for gap in case.gaps:
        if gap.code == "unknown_event":
            fact_code = "unknown_event_present"
        elif gap.code in {
            "redacted_event",
            "redacted_object",
            "event_payload_unavailable",
            "captured_object_unavailable",
        }:
            fact_code = "redaction_gap_present"
        else:
            fact_code = "freshness_gap_present"
        classes[fact_code].update(gap.subject_refs)
    observed = tuple(
        _fact(code, *refs) for code, values in classes.items() if (refs := _refs(values))
    )
    subjects = _refs(ref for fact in observed for ref in fact.subject_refs)
    if not subjects:
        return []
    if classes["redaction_gap_present"]:
        redaction_codes = {gap.code for gap in case.gaps if gap.subject_refs}
        availability = (
            FrozenSourceAvailability.REDACTED_AT_SOURCE
            if redaction_codes & {"redacted_event", "redacted_object"}
            else FrozenSourceAvailability.UNAVAILABLE_AT_FREEZE
        )
    elif classes["unknown_event_present"] or classes["freshness_gap_present"]:
        availability = FrozenSourceAvailability.NOT_RECORDED
    else:
        availability = FrozenSourceAvailability.AVAILABLE
    return [
        build_policy_assessment(
            case,
            WORK_INTEGRITY_POLICY_PACK,
            FindingKind.LEDGER_STALE_OR_INCOMPLETE,
            subjects,
            observed,
            source_availability=availability,
        )
    ]


def _response_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for finding_id, response_record in case.projection.responses.items():
        response = response_record.payload
        finding_record = case.projection.findings.get(finding_id)
        if (
            response is None
            or response.disposition
            not in {ResponseDisposition.REJECTED, ResponseDisposition.WAIVED}
            or finding_record is None
            or finding_record.payload is None
        ):
            continue
        finding = finding_record.payload
        # A response answers the finding at the frontier that carries the finding's own record,
        # which necessarily follows the subject the check tested. Only a response aimed at a state
        # older than that subject answers something the finding was never about.
        stale = response.finding_frontier.sequence < finding.subject_frontier.sequence
        insufficient = not _response_support_admissible(case, response.evidence_refs)
        # This pack stays a closed rule table: it never inspects whether another pack would also
        # report this response. A current unsupported rejection of a deterministic finding overlaps
        # research-evidence's questionable_finding_rejection, and the composition layer collapses
        # that overlap once it knows which packs actually ran.
        if not stale and not insufficient:
            continue
        if any(ref not in case.allowed_ids for ref in finding.subject_refs):
            continue
        response_event = response_record.source_event_id
        evidence_refs = tuple(ref for ref in response.evidence_refs if ref in case.allowed_ids)
        present_refs = _refs((finding_id, response_event, *evidence_refs))
        observed: list[FindingFact] = [FindingFact(WORK_RESPONSE_PRESENT_FACT, present_refs)]
        missing: list[FindingFact] = []
        if stale:
            observed.append(_fact("response_state_stale", finding_id, response_event))
        if insufficient:
            missing.append(_fact("response_basis_insufficient", finding_id, response_event))
        compared = _refs(evidence_refs)
        output.append(
            build_policy_assessment(
                case,
                WORK_INTEGRITY_POLICY_PACK,
                FindingKind.WEAK_OR_STALE_RESPONSE,
                finding.subject_refs,
                tuple(observed),
                tuple(missing),
                source_availability=(
                    policy_source_availability(case, compared)
                    if compared
                    else FrozenSourceAvailability.AVAILABLE
                ),
            )
        )
    return output


def work_integrity_findings(
    case: DeterministicCase,
) -> tuple[DeterministicAssessment, ...]:
    """Evaluate the closed work-integrity rule table without I/O."""

    if type(case) is not DeterministicCase:
        raise ValueError("policy_wiring_invalid")
    by_rule = (
        _completion_findings(case),
        _requested_item_findings(case),
        _failed_work_findings(case),
        _unsupported_claim_findings(case),
        _orphan_result_findings(case),
        _unresolved_action_findings(case),
        _stale_evidence_findings(case),
        _contradiction_findings(case),
        _ledger_finding(case),
        _response_findings(case),
    )
    if len(by_rule) != len(_RULE_ORDER):
        raise ValueError("policy_wiring_invalid")
    output: list[DeterministicAssessment] = []
    for kind, assessments in zip(_RULE_ORDER, by_rule, strict=True):
        deduped: dict[tuple[str, ...], DeterministicAssessment] = {}
        for assessment in assessments:
            if assessment.candidate.kind is not kind:
                raise ValueError("policy_wiring_invalid")
            key = tuple(assessment.candidate.subject_refs)
            if key in deduped:
                if deduped[key] != assessment:
                    raise ValueError("policy_wiring_invalid")
                continue
            deduped[key] = assessment
        output.extend(
            deduped[key]
            for key in sorted(deduped, key=lambda refs: tuple(_ascii(ref) for ref in refs))
        )
    return tuple(output)
