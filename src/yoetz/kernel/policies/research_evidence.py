"""Deterministic research-evidence policy pack."""

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
from yoetz.domain.findings import FindingKind, FindingOrigin
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

__all__ = [
    "RESEARCH_EVIDENCE_FACT_CODES",
    "RESEARCH_EVIDENCE_POLICY_ID",
    "RESEARCH_EVIDENCE_POLICY_PACK",
    "RESEARCH_EVIDENCE_POLICY_VERSION",
    "research_evidence_findings",
]

RESEARCH_EVIDENCE_POLICY_ID: Final = "research-evidence"
RESEARCH_EVIDENCE_POLICY_VERSION: Final = "0.1.0"
RESEARCH_EVIDENCE_POLICY_PACK: Final = PolicyPack(
    RESEARCH_EVIDENCE_POLICY_ID,
    RESEARCH_EVIDENCE_POLICY_VERSION,
)
RESEARCH_EVIDENCE_FACT_CODES: Final = frozenset(
    {
        "claim_support_present",
        "claim_support_mismatch",
        "captured_state_present",
        "account_state_mismatch",
        "material_limitation_present",
        "limitation_disclosure_absent",
        "finding_rejection_present",
        "rejection_basis_insufficient",
    }
)

_RULE_ORDER: Final = (
    FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
    FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
    FindingKind.MATERIAL_LIMITATION_OMITTED,
    FindingKind.QUESTIONABLE_FINDING_REJECTION,
)
_MATERIAL_GAPS: Final = frozenset(
    {
        "redacted_event",
        "redacted_object",
        "event_payload_unavailable",
        "captured_object_unavailable",
        "missing_ref",
        "unknown_event",
    }
)


def _ascii(value: str) -> bytes:
    return value.encode("ascii", errors="strict")


def _refs(values: Iterable[FindingBasisRef]) -> tuple[FindingBasisRef, ...]:
    return tuple(sorted(set(values), key=_ascii))


def _fact(code: str, *refs: FindingBasisRef) -> FindingFact:
    return FindingFact(code, _refs(refs))


def _support_state(case: DeterministicCase, ref: FindingBasisRef) -> SubjectStateRef | None:
    if ref.startswith("evd_"):
        record = case.projection.evidence.get(EvidenceId(ref))
        return None if record is None or record.payload is None else record.payload.subject_state
    if ref.startswith("res_"):
        record = case.projection.results.get(ResultId(ref))
        return None if record is None or record.payload is None else record.payload.subject_state
    return None


def _typed_support_mismatch(
    case: DeterministicCase,
    claim_kind: ClaimKind,
    claim_state: SubjectStateRef | None,
    ref: FindingBasisRef,
) -> tuple[bool, SubjectStateRelation]:
    if ref not in case.allowed_ids:
        return False, SubjectStateRelation.UNKNOWN
    relation = subject_state_relation(_support_state(case, ref), claim_state)
    if relation is SubjectStateRelation.DIFFERENT:
        return True, relation
    if ref.startswith("res_"):
        record = case.projection.results.get(ResultId(ref))
        if (
            record is not None
            and record.payload is not None
            and claim_kind is ClaimKind.COMPLETION
            and record.payload.outcome
            in {ResultOutcome.FAILURE, ResultOutcome.PARTIAL, ResultOutcome.UNKNOWN}
        ):
            return True, relation
    if ref.startswith("obl_"):
        record = case.projection.obligations.get(ObligationId(ref))
        if (
            record is not None
            and record.payload is not None
            and claim_kind is ClaimKind.COMPLETION
            and record.payload.status is ObligationStatus.OPEN
            and record.plan_change is not ObligationChangeKind.WAIVED
        ):
            return True, relation
    return False, relation


def _exact_state_mismatch(
    left: SubjectStateRef | None,
    right: SubjectStateRef | None,
) -> bool:
    if left is None or right is None:
        return False
    if left.tree_digest is not None and right.tree_digest is not None:
        return left.tree_digest != right.tree_digest
    if left.diff_digest is not None and right.diff_digest is not None:
        return left.diff_digest != right.diff_digest
    return False


def _coverage_admissible(case: DeterministicCase, ref: FindingBasisRef) -> bool:
    coverage = case.coverage_by_ref.get(ref)
    return coverage is not None and not _MATERIAL_GAPS & set(coverage.known_gaps)


def _response_support_admissible(
    case: DeterministicCase,
    refs: tuple[EvidenceId | ResultId, ...],
) -> bool:
    for ref in refs:
        if ref not in case.allowed_ids or not _coverage_admissible(case, ref):
            continue
        if ref.startswith("evd_"):
            record = case.projection.evidence.get(EvidenceId(ref))
        else:
            record = case.projection.results.get(ResultId(ref))
        if record is not None and record.payload is not None:
            return True
    return False


def _support_mismatch_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for claim_id, record in case.projection.claims.items():
        claim = record.payload
        if claim is None:
            continue
        for raw_ref in claim.supporting_refs:
            ref = cast(FindingBasisRef, raw_ref)
            mismatch, relation = _typed_support_mismatch(
                case,
                claim.claim_kind,
                claim.subject_state,
                ref,
            )
            if not mismatch:
                continue
            fact_refs = _refs((claim_id, ref))
            output.append(
                build_policy_assessment(
                    case,
                    RESEARCH_EVIDENCE_POLICY_PACK,
                    FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
                    _refs((claim_id, policy_public_root(case, ref))),
                    (
                        FindingFact("claim_support_present", fact_refs),
                        FindingFact("claim_support_mismatch", fact_refs),
                    ),
                    subject_state_relation=relation,
                    source_availability=policy_source_availability(case, fact_refs),
                )
            )
    return output


def _diff_mismatch_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    for claim_id, record in case.projection.claims.items():
        claim = record.payload
        if claim is None or claim.subject_state is None:
            continue
        for raw_ref in claim.supporting_refs:
            ref = cast(FindingBasisRef, raw_ref)
            state = _support_state(case, ref)
            if not _exact_state_mismatch(state, claim.subject_state):
                continue
            fact_refs = _refs((claim_id, ref))
            output.append(
                build_policy_assessment(
                    case,
                    RESEARCH_EVIDENCE_POLICY_PACK,
                    FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
                    _refs((claim_id, policy_public_root(case, ref))),
                    (
                        FindingFact("captured_state_present", fact_refs),
                        FindingFact("account_state_mismatch", fact_refs),
                    ),
                    subject_state_relation=SubjectStateRelation.DIFFERENT,
                    source_availability=policy_source_availability(case, fact_refs),
                )
            )
    return output


def _limiting_refs(case: DeterministicCase) -> tuple[FindingBasisRef, ...]:
    limitations: set[FindingBasisRef] = set()
    for result_id, record in case.projection.results.items():
        if record.payload is not None and record.payload.outcome in {
            ResultOutcome.FAILURE,
            ResultOutcome.PARTIAL,
            ResultOutcome.UNKNOWN,
        }:
            limitations.add(result_id)
    for ref, coverage in case.coverage_by_ref.items():
        if ref.startswith(("obl_", "res_", "evd_")) and _MATERIAL_GAPS & set(coverage.known_gaps):
            limitations.add(ref)
    return _refs(limitations)


def _limitation_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
    output: list[DeterministicAssessment] = []
    limiting = _limiting_refs(case)
    rootless_material_gap = any(
        not gap.subject_refs and gap.code in _MATERIAL_GAPS for gap in case.gaps
    )
    for claim_id, record in case.projection.claims.items():
        claim = record.payload
        if claim is None or claim.claim_kind is not ClaimKind.COMPLETION:
            continue
        for limiting_ref in limiting:
            if limiting_ref in claim.supporting_refs:
                continue
            fact_refs = _refs((claim_id, limiting_ref))
            output.append(
                build_policy_assessment(
                    case,
                    RESEARCH_EVIDENCE_POLICY_PACK,
                    FindingKind.MATERIAL_LIMITATION_OMITTED,
                    _refs((claim_id, policy_public_root(case, limiting_ref))),
                    (FindingFact("material_limitation_present", fact_refs),),
                    (FindingFact("limitation_disclosure_absent", fact_refs),),
                    source_availability=policy_source_availability(case, (limiting_ref,)),
                )
            )
        if rootless_material_gap:
            output.append(
                build_policy_assessment(
                    case,
                    RESEARCH_EVIDENCE_POLICY_PACK,
                    FindingKind.MATERIAL_LIMITATION_OMITTED,
                    (claim_id,),
                    (_fact("material_limitation_present", claim_id),),
                    (_fact("limitation_disclosure_absent", claim_id),),
                    source_availability=FrozenSourceAvailability.NOT_RECORDED,
                )
            )
    return output


def _rejection_findings(case: DeterministicCase) -> list[DeterministicAssessment]:
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
            or finding_record.payload.origin is not FindingOrigin.DETERMINISTIC
            or _response_support_admissible(case, response.evidence_refs)
        ):
            continue
        finding = finding_record.payload
        # The response must answer the finding at or after the subject the check tested; a response
        # aimed at an older state is a different, weaker claim that work integrity reports instead.
        if response.finding_frontier.sequence < finding.subject_frontier.sequence or any(
            ref not in case.allowed_ids for ref in finding.subject_refs
        ):
            continue
        response_event = response_record.source_event_id
        evidence_refs = tuple(ref for ref in response.evidence_refs if ref in case.allowed_ids)
        present_refs = _refs((finding_id, response_event, *evidence_refs))
        compared = _refs(evidence_refs)
        output.append(
            build_policy_assessment(
                case,
                RESEARCH_EVIDENCE_POLICY_PACK,
                FindingKind.QUESTIONABLE_FINDING_REJECTION,
                finding.subject_refs,
                (FindingFact("finding_rejection_present", present_refs),),
                (_fact("rejection_basis_insufficient", finding_id, response_event),),
                source_availability=(
                    policy_source_availability(case, compared)
                    if compared
                    else FrozenSourceAvailability.AVAILABLE
                ),
            )
        )
    return output


def research_evidence_findings(
    case: DeterministicCase,
) -> tuple[DeterministicAssessment, ...]:
    """Evaluate the closed research-evidence rule table without I/O."""

    if type(case) is not DeterministicCase:
        raise ValueError("policy_wiring_invalid")
    by_rule = (
        _support_mismatch_findings(case),
        _diff_mismatch_findings(case),
        _limitation_findings(case),
        _rejection_findings(case),
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
