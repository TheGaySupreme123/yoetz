"""Pure deterministic case construction and versioned policy dispatch."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, Literal, cast

from yoetz.domain.events import (
    MAX_REF_LIST,
    AcceptedEvent,
    ClaimKind,
    EventSchema,
    EvidenceContentAvailability,
    LedgerRecord,
    RedactionState,
    UnknownEvent,
    decode_payload,
    encode_payload,
)
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CandidateFinding,
    FindingKind,
    FindingOrigin,
)
from yoetz.domain.receipts import (
    COMPLETION_SCOPE_DECLARED_NONE_GAP,
    COMPLETION_SCOPE_UNDECLARED_GAP,
)
from yoetz.domain.values import (
    ActionId,
    ClaimId,
    EventId,
    EvidenceId,
    FindingId,
    Frontier,
    ObjectId,
    ObligationId,
    ResultId,
    SubjectStateRelation,
    action_id,
    claim_id,
    event_id,
    evidence_id,
    finding_id,
    freeze_json,
    frontier_from_json,
    object_id,
    obligation_id,
    result_id,
    timestamp_from_string,
    validate_sha256_digest,
)
from yoetz.kernel.plan_scope import current_plan_scope
from yoetz.kernel.projections import (
    ProjectionRecord,
    ProjectionState,
    projection_from_snapshot,
    projection_snapshot,
)
from yoetz.kernel.reducers import (
    ReplayIndex,
    empty_replay_index,
    extend_replay_index,
    replay,
)
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.coverage import (
    EVIDENCE_IMMUTABILITY_ORDER,
    LEDGER_FRESHNESS_ORDER,
    ArtifactObservation,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
    coverage_from_json,
    coverage_to_json,
    weakest,
)

__all__ = [
    "DETERMINISTIC_FINDING_TEMPLATES",
    "EVIDENCE_PROVENANCE_GAPS",
    "CaseAvailabilityFacts",
    "CaseGap",
    "DeterministicAssessment",
    "DeterministicCase",
    "DeterministicFindingTemplate",
    "DeterministicPolicyResult",
    "FindingBasis",
    "FindingBasisRef",
    "FindingFact",
    "FrozenHistoryEvent",
    "FrozenSourceAvailability",
    "MAX_FROZEN_HISTORY_BYTES",
    "MAX_FROZEN_HISTORY_EVENTS",
    "PolicyPack",
    "UnavailableCapturedObject",
    "build_deterministic_case",
    "deterministic_case_from_json",
    "deterministic_case_to_json",
    "finding_basis_from_json",
    "finding_basis_to_json",
    "finding_basis_to_status_json",
    "render_deterministic_finding_text",
    "run_deterministic_policies",
]

type FindingBasisRef = (
    EventId | ObligationId | ClaimId | ActionId | ResultId | EvidenceId | FindingId
)
type PublicSubjectRef = EventId | ObligationId | ClaimId

_MAX_SAFE_INTEGER: Final = 2**53 - 1
_CODE_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_POLICY_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]{0,127}$", re.ASCII)
_VERSION_PATTERN: Final = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$",
    re.ASCII,
)
MAX_FROZEN_HISTORY_EVENTS: Final = 64
MAX_FROZEN_HISTORY_BYTES: Final = 512 * 1024
_SEMANTIC_HISTORY_FAMILIES: Final = frozenset(
    {
        "action_recorded",
        "check_recorded",
        "claim_recorded",
        "decision_recorded",
        "evidence_recorded",
        "finding_recorded",
        "obligation_published",
        "plan_published",
        "plan_revised",
        "response_recorded",
        "result_recorded",
    }
)


def _invalid_case() -> ValueError:
    return ValueError("deterministic_case_invalid")


def _invalid_basis() -> ValueError:
    return ValueError("finding_basis_invalid")


def _invalid_policy() -> ValueError:
    return ValueError("policy_wiring_invalid")


def _ascii_key(value: str) -> bytes:
    try:
        return value.encode("ascii", errors="strict")
    except UnicodeEncodeError as exc:
        raise _invalid_case() from exc


def _sorted_unique[T: str](values: Iterable[T]) -> tuple[T, ...]:
    return tuple(sorted(set(values), key=_ascii_key))


def _basis_ref(value: object) -> FindingBasisRef:
    if type(value) is not str:
        raise _invalid_basis()
    try:
        if value.startswith("evt_"):
            return event_id(value)
        if value.startswith("obl_"):
            return obligation_id(value)
        if value.startswith("clm_"):
            return claim_id(value)
        if value.startswith("act_"):
            return action_id(value)
        if value.startswith("res_"):
            return result_id(value)
        if value.startswith("evd_"):
            return evidence_id(value)
        if value.startswith("fnd_"):
            return finding_id(value)
    except ValueError as exc:
        raise _invalid_basis() from exc
    raise _invalid_basis()


def _public_ref(value: object) -> PublicSubjectRef:
    ref = _basis_ref(value)
    if ref.startswith("evt_"):
        return event_id(ref)
    if ref.startswith("obl_"):
        return obligation_id(ref)
    if ref.startswith("clm_"):
        return claim_id(ref)
    raise _invalid_basis()


def _validated_ref_tuple(
    value: object,
    *,
    public_only: bool = False,
    allow_empty: bool = False,
) -> tuple[FindingBasisRef, ...] | tuple[PublicSubjectRef, ...]:
    if type(value) is not tuple:
        raise _invalid_basis()
    raw = cast(tuple[object, ...], value)
    if not (0 if allow_empty else 1) <= len(raw) <= MAX_REF_LIST:
        raise _invalid_basis()
    refs = tuple((_public_ref(item) if public_only else _basis_ref(item)) for item in raw)
    if refs != _sorted_unique(refs):
        raise _invalid_basis()
    return refs


@dataclass(frozen=True, slots=True)
class PolicyPack:
    policy_id: str
    policy_version: str

    def __post_init__(self) -> None:
        if (
            type(self.policy_id) is not str
            or _POLICY_PATTERN.fullmatch(self.policy_id) is None
            or type(self.policy_version) is not str
            or _VERSION_PATTERN.fullmatch(self.policy_version) is None
        ):
            raise _invalid_policy()


@dataclass(frozen=True, slots=True)
class UnavailableCapturedObject:
    source_event_id: EventId
    object_id: ObjectId

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "source_event_id", event_id(self.source_event_id))
            object.__setattr__(self, "object_id", object_id(self.object_id))
        except ValueError as exc:
            raise _invalid_case() from exc


@dataclass(frozen=True, slots=True)
class CaseAvailabilityFacts:
    unavailable_event_ids: tuple[EventId, ...] = ()
    unavailable_captured_objects: tuple[UnavailableCapturedObject, ...] = ()

    def __post_init__(self) -> None:
        if type(self.unavailable_event_ids) is not tuple:
            raise _invalid_case()
        try:
            event_ids = tuple(event_id(value) for value in self.unavailable_event_ids)
        except ValueError as exc:
            raise _invalid_case() from exc
        if event_ids != _sorted_unique(event_ids):
            raise _invalid_case()
        if type(self.unavailable_captured_objects) is not tuple or any(
            type(item) is not UnavailableCapturedObject
            for item in self.unavailable_captured_objects
        ):
            raise _invalid_case()
        objects = self.unavailable_captured_objects
        ordered = tuple(
            sorted(
                set(objects),
                key=lambda item: (_ascii_key(item.source_event_id), _ascii_key(item.object_id)),
            )
        )
        if objects != ordered:
            raise _invalid_case()
        object.__setattr__(self, "unavailable_event_ids", event_ids)


class FrozenSourceAvailability(str, Enum):  # noqa: UP042 - exact wire enum base
    AVAILABLE = "available"
    NOT_RECORDED = "not_recorded"
    UNAVAILABLE_AT_FREEZE = "unavailable_at_freeze"
    REDACTED_AT_SOURCE = "redacted_at_source"


@dataclass(frozen=True, slots=True)
class FindingFact:
    fact_code: str
    subject_refs: tuple[FindingBasisRef, ...]

    def __post_init__(self) -> None:
        if type(self.fact_code) is not str or _CODE_PATTERN.fullmatch(self.fact_code) is None:
            raise _invalid_basis()
        refs = _validated_ref_tuple(self.subject_refs)
        object.__setattr__(self, "subject_refs", cast(tuple[FindingBasisRef, ...], refs))


def _fact_key(fact: FindingFact) -> tuple[bytes, tuple[bytes, ...]]:
    return (_ascii_key(fact.fact_code), tuple(_ascii_key(ref) for ref in fact.subject_refs))


def _validate_fact_tuple(value: object, *, minimum: int) -> tuple[FindingFact, ...]:
    if type(value) is not tuple:
        raise _invalid_basis()
    facts = cast(tuple[object, ...], value)
    if not minimum <= len(facts) <= 33 or any(type(fact) is not FindingFact for fact in facts):
        raise _invalid_basis()
    typed = cast(tuple[FindingFact, ...], facts)
    if typed != tuple(sorted(typed, key=_fact_key)) or len(
        {fact.fact_code for fact in typed}
    ) != len(typed):
        raise _invalid_basis()
    return typed


@dataclass(frozen=True, slots=True)
class FindingBasis:
    rule_id: str
    observed_facts: tuple[FindingFact, ...]
    required_but_missing_facts: tuple[FindingFact, ...]
    subject_state_relation: SubjectStateRelation
    source_availability: FrozenSourceAvailability
    coverage_gaps: tuple[str, ...]
    supporting_refs: tuple[FindingBasisRef, ...]

    def __post_init__(self) -> None:
        if type(self.rule_id) is not str or len(self.rule_id) > 256 or self.rule_id.count("/") != 1:
            raise _invalid_basis()
        policy, rule = self.rule_id.split("/", 1)
        if _POLICY_PATTERN.fullmatch(policy) is None or _CODE_PATTERN.fullmatch(rule) is None:
            raise _invalid_basis()
        observed = _validate_fact_tuple(self.observed_facts, minimum=1)
        missing = _validate_fact_tuple(self.required_but_missing_facts, minimum=0)
        object.__setattr__(self, "observed_facts", observed)
        object.__setattr__(self, "required_but_missing_facts", missing)
        if type(self.subject_state_relation) is not SubjectStateRelation:
            raise _invalid_basis()
        if type(self.source_availability) is not FrozenSourceAvailability:
            raise _invalid_basis()
        if (
            type(self.coverage_gaps) is not tuple
            or not 0 <= len(self.coverage_gaps) <= MAX_REF_LIST
        ):
            raise _invalid_basis()
        for gap in self.coverage_gaps:
            if type(gap) is not str or _CODE_PATTERN.fullmatch(gap) is None:
                raise _invalid_basis()
        if self.coverage_gaps != _sorted_unique(self.coverage_gaps):
            raise _invalid_basis()
        refs = cast(
            tuple[FindingBasisRef, ...],
            _validated_ref_tuple(self.supporting_refs),
        )
        expected = _sorted_unique(ref for fact in self.observed_facts for ref in fact.subject_refs)
        if refs != expected:
            raise _invalid_basis()
        object.__setattr__(self, "supporting_refs", refs)


@dataclass(frozen=True, slots=True)
class DeterministicFindingTemplate:
    summary: str
    next_action: str

    def __post_init__(self) -> None:
        if (
            type(self.summary) is not str
            or not self.summary
            or type(self.next_action) is not str
            or not self.next_action
        ):
            raise _invalid_policy()


DETERMINISTIC_FINDING_TEMPLATES: Final[
    MappingProxyType[FindingKind, DeterministicFindingTemplate]
] = MappingProxyType(
    {
        FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS: DeterministicFindingTemplate(
            "A completion claim covers an obligation that remains open.",
            "Resolve the obligation or revise the completion claim.",
        ),
        FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED: DeterministicFindingTemplate(
            "A requested item has no recorded attempt.",
            "Attempt the requested item or revise its obligation.",
        ),
        FindingKind.FAILED_WORK_OMITTED: DeterministicFindingTemplate(
            "Recorded failed work is omitted from the published account.",
            "Disclose the failed work or revise the account.",
        ),
        FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE: DeterministicFindingTemplate(
            "A recorded claim has no admissible supporting evidence.",
            "Provide admissible support or revise the claim.",
        ),
        FindingKind.RESULT_WITHOUT_ACTION: DeterministicFindingTemplate(
            "A recorded result has no linked action.",
            "Publish the linked action or correct the result record.",
        ),
        FindingKind.ACTION_WITHOUT_RESULT: DeterministicFindingTemplate(
            "A recorded action has no linked result.",
            "Record the result or state the attempt as unresolved.",
        ),
        FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE: DeterministicFindingTemplate(
            "The cited evidence predates a materially changed subject state.",
            "Run a check against the current state.",
        ),
        FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED: DeterministicFindingTemplate(
            "Explicitly disputed claims remain structurally unresolved.",
            "Record a structural resolution or supersession.",
        ),
        FindingKind.LEDGER_STALE_OR_INCOMPLETE: DeterministicFindingTemplate(
            "The ledger is too incomplete for a current conclusion.",
            "Treat the conclusion as coverage-limited.",
        ),
        FindingKind.WEAK_OR_STALE_RESPONSE: DeterministicFindingTemplate(
            "A finding response lacks current admissible support.",
            "Provide current admissible response evidence.",
        ),
        FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM: DeterministicFindingTemplate(
            "The cited evidence does not support the recorded claim.",
            "Provide relevant evidence or revise the claim.",
        ),
        FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT: DeterministicFindingTemplate(
            "Recorded subject-state digests contradict the published account.",
            "Revise the account or publish matching state evidence.",
        ),
        FindingKind.MATERIAL_LIMITATION_OMITTED: DeterministicFindingTemplate(
            "A material recorded limitation is omitted from the published account.",
            "Disclose the limitation or revise the account.",
        ),
        FindingKind.QUESTIONABLE_FINDING_REJECTION: DeterministicFindingTemplate(
            "A deterministic finding was rejected without admissible support.",
            "Provide current evidence for the rejection.",
        ),
    }
)
if frozenset(DETERMINISTIC_FINDING_TEMPLATES) != frozenset(FindingKind):
    raise _invalid_policy()


EVIDENCE_PROVENANCE_GAPS: Final[frozenset[str]] = frozenset(
    {
        "evidence_content_digest_only",
        "evidence_content_withheld",
        "evidence_digest_subject_legacy_unknown",
    }
)


def render_deterministic_finding_text(
    kind: FindingKind,
    subject_refs: tuple[PublicSubjectRef, ...],
    gap_codes: tuple[str, ...] = (),
) -> tuple[str, str]:
    if type(kind) is not FindingKind or type(gap_codes) is not tuple:
        raise _invalid_policy()
    refs = cast(
        tuple[PublicSubjectRef, ...],
        _validated_ref_tuple(subject_refs, public_only=True),
    )
    template = DETERMINISTIC_FINDING_TEMPLATES[kind]
    detail = f"Subjects: {', '.join(refs)}. Main agent: {template.next_action}"
    if kind is FindingKind.LEDGER_STALE_OR_INCOMPLETE and gap_codes:
        detail = (
            f"Subjects: {', '.join(refs)}. Gaps: {', '.join(gap_codes)}."
            f" Main agent: {template.next_action}"
        )
        if EVIDENCE_PROVENANCE_GAPS & set(gap_codes):
            detail = (
                f"{detail} An evidence-provenance gap is not resolved by a finding response:"
                " record content-bearing evidence or accept the gap in the receipt."
            )
    return template.summary, detail


@dataclass(frozen=True, slots=True)
class DeterministicAssessment:
    candidate: CandidateFinding
    basis: FindingBasis

    def __post_init__(self) -> None:
        if type(self.candidate) is not CandidateFinding or type(self.basis) is not FindingBasis:
            raise _invalid_policy()
        candidate = self.candidate
        if (
            candidate.origin is not FindingOrigin.DETERMINISTIC
            or candidate.provenance is not None
            or self.basis.rule_id != f"{candidate.policy_id}/{candidate.kind.value}"
        ):
            raise _invalid_policy()
        summary, detail = render_deterministic_finding_text(
            candidate.kind,
            candidate.subject_refs,
            self.basis.coverage_gaps,
        )
        if candidate.summary != summary or candidate.detail != detail:
            raise _invalid_policy()


@dataclass(frozen=True, slots=True)
class DeterministicPolicyResult:
    assessments: tuple[DeterministicAssessment, ...]

    def __post_init__(self) -> None:
        if type(self.assessments) is not tuple or any(
            type(item) is not DeterministicAssessment for item in self.assessments
        ):
            raise _invalid_policy()
        keys = tuple(
            (
                item.candidate.policy_id,
                item.basis.rule_id,
                item.candidate.subject_refs,
            )
            for item in self.assessments
        )
        if len(keys) != len(set(keys)):
            raise _invalid_policy()


def finding_basis_to_status_json(
    assessment: DeterministicAssessment,
) -> dict[str, object]:
    if type(assessment) is not DeterministicAssessment:
        raise _invalid_basis()
    candidate = assessment.candidate
    basis = assessment.basis
    if basis.rule_id != f"{candidate.policy_id}/{candidate.kind.value}":
        raise _invalid_basis()
    observed_codes = _sorted_unique(fact.fact_code for fact in basis.observed_facts)
    observed_refs = _sorted_unique(
        ref for fact in basis.observed_facts for ref in fact.subject_refs
    )
    missing_codes = _sorted_unique(fact.fact_code for fact in basis.required_but_missing_facts)
    availability = {
        FrozenSourceAvailability.AVAILABLE: "available",
        FrozenSourceAvailability.NOT_RECORDED: "not_recorded",
        FrozenSourceAvailability.UNAVAILABLE_AT_FREEZE: "unavailable",
        FrozenSourceAvailability.REDACTED_AT_SOURCE: "redacted",
    }[basis.source_availability]
    evidence_refs = tuple(ref for ref in basis.supporting_refs if ref.startswith(("evd_", "res_")))
    return {
        "rule_id": candidate.kind.value,
        "observed_fact_codes": list(observed_codes),
        "observed_refs": list(observed_refs),
        "required_missing_fact_codes": list(missing_codes),
        "subject_state_relation": basis.subject_state_relation.value,
        "frozen_source_availability": availability,
        "coverage_gaps": list(basis.coverage_gaps),
        "evidence_refs": list(evidence_refs),
    }


@dataclass(frozen=True, slots=True)
class CaseGap:
    marker: str
    code: str
    subject_refs: tuple[PublicSubjectRef, ...]

    def __post_init__(self) -> None:
        if (
            type(self.marker) is not str
            or not self.marker
            or len(self.marker.encode("utf-8")) > 8_192
            or type(self.code) is not str
            or _CODE_PATTERN.fullmatch(self.code) is None
        ):
            raise _invalid_case()
        refs = cast(
            tuple[PublicSubjectRef, ...],
            _validated_ref_tuple(self.subject_refs, public_only=True, allow_empty=True),
        )
        object.__setattr__(self, "subject_refs", refs)


@dataclass(frozen=True, slots=True)
class FrozenHistoryEvent:
    """One material accepted event retained in the bounded frozen history slice."""

    event_id: EventId
    schema_name: str
    schema_version: str
    ingestion_sequence: int
    occurred_at: str
    payload_digest: str
    content_visibility: Literal["available", "not_recorded", "not_selected", "redacted_never_send"]
    payload: JsonValue | None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "event_id", event_id(self.event_id))
            schema = EventSchema(self.schema_name, self.schema_version)
            validate_sha256_digest(self.payload_digest)
            timestamp_from_string(self.occurred_at)
        except (TypeError, ValueError) as exc:
            raise _invalid_case() from exc
        if (
            schema.name not in _SEMANTIC_HISTORY_FAMILIES
            or type(self.ingestion_sequence) is not int
            or not 1 <= self.ingestion_sequence <= _MAX_SAFE_INTEGER
            or self.content_visibility
            not in {"available", "not_recorded", "not_selected", "redacted_never_send"}
        ):
            raise _invalid_case()
        if self.content_visibility == "available":
            if self.payload is None:
                raise _invalid_case()
            try:
                payload = freeze_json(self.payload)
                decoded = decode_payload(schema, payload)
            except (TypeError, ValueError) as exc:
                raise _invalid_case() from exc
            if canonical_digest(encode_payload(decoded)) != self.payload_digest:
                raise _invalid_case()
            object.__setattr__(self, "payload", payload)
        elif self.payload is not None:
            raise _invalid_case()


@dataclass(frozen=True, slots=True)
class DeterministicCase:
    projection: ProjectionState
    frontier: Frontier
    availability: CaseAvailabilityFacts
    allowed_ids: frozenset[FindingBasisRef]
    coverage_by_ref: Mapping[FindingBasisRef, Coverage]
    gaps: tuple[CaseGap, ...]
    history: tuple[FrozenHistoryEvent, ...] = ()
    history_availability: Literal["available", "not_recorded"] = "not_recorded"
    history_omitted_before_count: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.projection) is not ProjectionState
            or type(self.frontier) is not Frontier
            or self.frontier.sequence != self.projection.frontier
            or self.frontier.head_digest != self.projection.head_digest
            or type(self.availability) is not CaseAvailabilityFacts
            or type(self.allowed_ids) is not frozenset
        ):
            raise _invalid_case()
        try:
            allowed = frozenset(_basis_ref(value) for value in self.allowed_ids)
        except ValueError as exc:
            raise _invalid_case() from exc
        if not isinstance(cast(object, self.coverage_by_ref), Mapping):
            raise _invalid_case()
        coverage: dict[FindingBasisRef, Coverage] = {}
        try:
            for raw_ref, value in self.coverage_by_ref.items():
                ref = _basis_ref(raw_ref)
                if type(value) is not Coverage:
                    raise _invalid_case()
                coverage[ref] = value
        except ValueError as exc:
            raise _invalid_case() from exc
        if frozenset(coverage) != allowed:
            raise _invalid_case()
        if type(self.gaps) is not tuple or any(type(gap) is not CaseGap for gap in self.gaps):
            raise _invalid_case()
        ordered_gaps = tuple(
            sorted(
                self.gaps,
                key=lambda gap: (
                    _ascii_key(gap.marker),
                    tuple(_ascii_key(ref) for ref in gap.subject_refs),
                ),
            )
        )
        if self.gaps != ordered_gaps or len({gap.marker for gap in self.gaps}) != len(self.gaps):
            raise _invalid_case()
        if (
            type(self.history) is not tuple
            or len(self.history) > MAX_FROZEN_HISTORY_EVENTS
            or any(type(item) is not FrozenHistoryEvent for item in self.history)
            or self.history_availability not in {"available", "not_recorded"}
            or type(self.history_omitted_before_count) is not int
            or not 0 <= self.history_omitted_before_count <= _MAX_SAFE_INTEGER
            or (
                self.history_availability == "not_recorded"
                and (self.history or self.history_omitted_before_count)
            )
        ):
            raise _invalid_case()
        history_keys = tuple(
            (item.ingestion_sequence, _ascii_key(item.event_id)) for item in self.history
        )
        history_bytes = sum(
            len(canonical_encode(item.payload)) for item in self.history if item.payload is not None
        )
        if (
            history_keys != tuple(sorted(history_keys))
            or len({item.event_id for item in self.history}) != len(self.history)
            or any(item.ingestion_sequence > self.frontier.sequence for item in self.history)
            or any(item.event_id not in allowed for item in self.history)
            or history_bytes > MAX_FROZEN_HISTORY_BYTES
        ):
            raise _invalid_case()
        object.__setattr__(self, "allowed_ids", allowed)
        object.__setattr__(self, "coverage_by_ref", MappingProxyType(coverage))


_LEGACY_CASE_JSON_KEYS: Final = frozenset(
    {"projection", "frontier", "availability", "allowed_ids", "coverage_by_ref", "gaps"}
)
_CASE_JSON_KEYS: Final = _LEGACY_CASE_JSON_KEYS | frozenset(
    {"history", "history_availability", "history_omitted_before_count"}
)


def _case_json_object(
    value: object,
    *,
    required: frozenset[str] | None = None,
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise _invalid_case()
    source = cast(Mapping[object, object], value)
    keys = tuple(source)
    if any(type(key) is not str for key in keys):
        raise _invalid_case()
    typed = cast(Mapping[str, JsonValue], source)
    if required is not None and frozenset(cast(tuple[str, ...], keys)) != required:
        raise _invalid_case()
    return typed


def _case_json_array(value: object) -> tuple[JsonValue, ...]:
    if type(value) is not tuple:
        raise _invalid_case()
    return cast(tuple[JsonValue, ...], value)


def _basis_json_object(
    value: object,
    *,
    required: frozenset[str],
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise _invalid_basis()
    source = cast(Mapping[object, object], value)
    keys = tuple(source)
    if any(type(key) is not str for key in keys):
        raise _invalid_basis()
    if frozenset(cast(tuple[str, ...], keys)) != required:
        raise _invalid_basis()
    return cast(Mapping[str, JsonValue], source)


def _basis_json_array(value: object) -> tuple[JsonValue, ...]:
    if type(value) is not tuple:
        raise _invalid_basis()
    return cast(tuple[JsonValue, ...], value)


def finding_basis_to_json(basis: FindingBasis) -> dict[str, JsonValue]:
    """Encode one complete finding basis without the lossy status projection."""

    if type(basis) is not FindingBasis:
        raise _invalid_basis()

    def encode_facts(facts: tuple[FindingFact, ...]) -> list[JsonValue]:
        return [
            {
                "fact_code": fact.fact_code,
                "subject_refs": list(fact.subject_refs),
            }
            for fact in facts
        ]

    return {
        "rule_id": basis.rule_id,
        "observed_facts": encode_facts(basis.observed_facts),
        "required_but_missing_facts": encode_facts(basis.required_but_missing_facts),
        "subject_state_relation": basis.subject_state_relation.value,
        "source_availability": basis.source_availability.value,
        "coverage_gaps": list(basis.coverage_gaps),
        "supporting_refs": list(basis.supporting_refs),
    }


def finding_basis_from_json(value: JsonValue) -> FindingBasis:
    """Decode and revalidate one complete, closed finding-basis JSON tree."""

    try:
        frozen = freeze_json(value)
        source = _basis_json_object(
            frozen,
            required=frozenset(
                {
                    "rule_id",
                    "observed_facts",
                    "required_but_missing_facts",
                    "subject_state_relation",
                    "source_availability",
                    "coverage_gaps",
                    "supporting_refs",
                }
            ),
        )

        def decode_facts(raw_facts: JsonValue) -> tuple[FindingFact, ...]:
            facts: list[FindingFact] = []
            for raw_fact in _basis_json_array(raw_facts):
                fact_source = _basis_json_object(
                    raw_fact,
                    required=frozenset({"fact_code", "subject_refs"}),
                )
                facts.append(
                    FindingFact(
                        fact_code=cast(str, fact_source["fact_code"]),
                        subject_refs=cast(
                            tuple[FindingBasisRef, ...],
                            _basis_json_array(fact_source["subject_refs"]),
                        ),
                    )
                )
            return tuple(facts)

        return FindingBasis(
            rule_id=cast(str, source["rule_id"]),
            observed_facts=decode_facts(source["observed_facts"]),
            required_but_missing_facts=decode_facts(source["required_but_missing_facts"]),
            subject_state_relation=SubjectStateRelation(
                cast(str, source["subject_state_relation"])
            ),
            source_availability=FrozenSourceAvailability(cast(str, source["source_availability"])),
            coverage_gaps=cast(
                tuple[str, ...],
                _basis_json_array(source["coverage_gaps"]),
            ),
            supporting_refs=cast(
                tuple[FindingBasisRef, ...],
                _basis_json_array(source["supporting_refs"]),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid_basis() from exc


def deterministic_case_to_json(case: DeterministicCase) -> dict[str, JsonValue]:
    """Encode one frozen deterministic case as its exact canonical JSON tree."""

    if type(case) is not DeterministicCase:
        raise _invalid_case()
    coverage: dict[str, JsonValue] = {
        str(ref): coverage_to_json(case.coverage_by_ref[ref])
        for ref in sorted(case.coverage_by_ref, key=_ascii_key)
    }
    return {
        "projection": projection_snapshot(case.projection),
        "frontier": case.frontier.as_wire(),
        "availability": {
            "unavailable_event_ids": list(case.availability.unavailable_event_ids),
            "unavailable_captured_objects": [
                {
                    "source_event_id": item.source_event_id,
                    "object_id": item.object_id,
                }
                for item in case.availability.unavailable_captured_objects
            ],
        },
        "allowed_ids": list(sorted(case.allowed_ids, key=_ascii_key)),
        "coverage_by_ref": coverage,
        "gaps": [
            {
                "marker": gap.marker,
                "code": gap.code,
                "subject_refs": list(gap.subject_refs),
            }
            for gap in case.gaps
        ],
        "history": [
            {
                "event_id": item.event_id,
                "schema_name": item.schema_name,
                "schema_version": item.schema_version,
                "ingestion_sequence": item.ingestion_sequence,
                "occurred_at": item.occurred_at,
                "payload_digest": item.payload_digest,
                "content_visibility": item.content_visibility,
                "payload": item.payload,
            }
            for item in case.history
        ],
        "history_availability": case.history_availability,
        "history_omitted_before_count": case.history_omitted_before_count,
    }


def deterministic_case_from_json(value: JsonValue) -> DeterministicCase:
    """Decode and revalidate one exact deterministic-case JSON tree."""

    try:
        frozen = freeze_json(value)
        source = _case_json_object(frozen)
        source_keys = frozenset(source)
        if source_keys not in {_CASE_JSON_KEYS, _LEGACY_CASE_JSON_KEYS}:
            raise _invalid_case()
        availability_source = _case_json_object(
            source["availability"],
            required=frozenset({"unavailable_event_ids", "unavailable_captured_objects"}),
        )
        unavailable_objects: list[UnavailableCapturedObject] = []
        for raw_object in _case_json_array(availability_source["unavailable_captured_objects"]):
            object_source = _case_json_object(
                raw_object,
                required=frozenset({"source_event_id", "object_id"}),
            )
            unavailable_objects.append(
                UnavailableCapturedObject(
                    source_event_id=cast(EventId, object_source["source_event_id"]),
                    object_id=cast(ObjectId, object_source["object_id"]),
                )
            )
        availability = CaseAvailabilityFacts(
            unavailable_event_ids=cast(
                tuple[EventId, ...],
                _case_json_array(availability_source["unavailable_event_ids"]),
            ),
            unavailable_captured_objects=tuple(unavailable_objects),
        )
        coverage_source = _case_json_object(source["coverage_by_ref"])
        coverage = {
            _basis_ref(raw_ref): coverage_from_json(raw_coverage)
            for raw_ref, raw_coverage in coverage_source.items()
        }
        allowed_refs = tuple(_basis_ref(item) for item in _case_json_array(source["allowed_ids"]))
        if allowed_refs != _sorted_unique(allowed_refs):
            raise _invalid_case()
        gaps: list[CaseGap] = []
        for raw_gap in _case_json_array(source["gaps"]):
            gap_source = _case_json_object(
                raw_gap,
                required=frozenset({"marker", "code", "subject_refs"}),
            )
            gaps.append(
                CaseGap(
                    marker=cast(str, gap_source["marker"]),
                    code=cast(str, gap_source["code"]),
                    subject_refs=cast(
                        tuple[PublicSubjectRef, ...],
                        _case_json_array(gap_source["subject_refs"]),
                    ),
                )
            )
        history: list[FrozenHistoryEvent] = []
        history_availability: Literal["available", "not_recorded"] = "not_recorded"
        history_omitted_before_count = 0
        if source_keys == _CASE_JSON_KEYS:
            for raw_item in _case_json_array(source["history"]):
                item = _case_json_object(
                    raw_item,
                    required=frozenset(
                        {
                            "event_id",
                            "schema_name",
                            "schema_version",
                            "ingestion_sequence",
                            "occurred_at",
                            "payload_digest",
                            "content_visibility",
                            "payload",
                        }
                    ),
                )
                history.append(
                    FrozenHistoryEvent(
                        event_id=cast(EventId, item["event_id"]),
                        schema_name=cast(str, item["schema_name"]),
                        schema_version=cast(str, item["schema_version"]),
                        ingestion_sequence=cast(int, item["ingestion_sequence"]),
                        occurred_at=cast(str, item["occurred_at"]),
                        payload_digest=cast(str, item["payload_digest"]),
                        content_visibility=cast(
                            Literal[
                                "available",
                                "not_recorded",
                                "not_selected",
                                "redacted_never_send",
                            ],
                            item["content_visibility"],
                        ),
                        payload=item["payload"],
                    )
                )
            history_availability = cast(
                Literal["available", "not_recorded"], source["history_availability"]
            )
            history_omitted_before_count = cast(int, source["history_omitted_before_count"])
        return DeterministicCase(
            projection=projection_from_snapshot(source["projection"]),
            frontier=frontier_from_json(source["frontier"]),
            availability=availability,
            allowed_ids=frozenset(allowed_refs),
            coverage_by_ref=coverage,
            gaps=tuple(gaps),
            history=tuple(history),
            history_availability=history_availability,
            history_omitted_before_count=history_omitted_before_count,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _invalid_case() from exc


def _projection_records(
    projection: ProjectionState,
) -> tuple[ProjectionRecord[object], ...]:
    records: list[ProjectionRecord[object]] = []
    for collection in (
        projection.plans,
        projection.obligations,
        projection.decisions,
        projection.assignments,
        projection.actions,
        projection.results,
        projection.evidence,
        projection.claims,
        projection.findings,
        projection.responses,
    ):
        records.extend(cast(Iterable[ProjectionRecord[object]], collection.values()))
    return tuple(records)


def _logical_sources(projection: ProjectionState) -> dict[FindingBasisRef, EventId]:
    sources: dict[FindingBasisRef, EventId] = {}
    for logical, record in projection.obligations.items():
        sources[logical] = record.source_event_id
    for logical, record in projection.actions.items():
        sources[logical] = record.source_event_id
    for logical, record in projection.results.items():
        sources[logical] = record.source_event_id
    for logical, record in projection.evidence.items():
        sources[logical] = record.source_event_id
    for logical, record in projection.claims.items():
        sources[logical] = record.source_event_id
    for logical, record in projection.findings.items():
        sources[logical] = record.source_event_id
    return sources


def _cap[T: Enum](value: T, limit: T, order: Mapping[T, int]) -> T:
    return value if order[value] <= order[limit] else limit


def _weaken_ref_coverage(
    base: Coverage,
    *,
    redacted_event: bool,
    unavailable_event: bool,
    redacted_object: bool,
    unavailable_object: bool,
    missing_ref: bool,
    unknown_event: bool,
    evidence_provenance_gaps: frozenset[str] = frozenset(),
) -> Coverage:
    observation = base.artifact_observation
    immutability = base.evidence_immutability
    freshness = base.ledger_freshness
    gaps = set(base.known_gaps)

    if redacted_event:
        observation = ArtifactObservation.PUBLISHED_ONLY
        immutability = _cap(
            immutability,
            EvidenceImmutability.METADATA_ONLY,
            EVIDENCE_IMMUTABILITY_ORDER,
        )
        freshness = _cap(
            freshness,
            LedgerFreshness.REDACTED_GAP,
            LEDGER_FRESHNESS_ORDER,
        )
        gaps.add("redacted_event")
    if unavailable_event:
        observation = ArtifactObservation.PUBLISHED_ONLY
        immutability = _cap(
            immutability,
            EvidenceImmutability.METADATA_ONLY,
            EVIDENCE_IMMUTABILITY_ORDER,
        )
        freshness = _cap(
            freshness,
            LedgerFreshness.REDACTED_GAP,
            LEDGER_FRESHNESS_ORDER,
        )
        gaps.add("event_payload_unavailable")
    if redacted_object:
        observation = ArtifactObservation.PUBLISHED_ONLY
        immutability = _cap(
            immutability,
            EvidenceImmutability.CONTENT_DIGEST,
            EVIDENCE_IMMUTABILITY_ORDER,
        )
        freshness = _cap(
            freshness,
            LedgerFreshness.REDACTED_GAP,
            LEDGER_FRESHNESS_ORDER,
        )
        gaps.add("redacted_object")
    if unavailable_object:
        observation = ArtifactObservation.PUBLISHED_ONLY
        immutability = _cap(
            immutability,
            EvidenceImmutability.CONTENT_DIGEST,
            EVIDENCE_IMMUTABILITY_ORDER,
        )
        freshness = _cap(
            freshness,
            LedgerFreshness.REDACTED_GAP,
            LEDGER_FRESHNESS_ORDER,
        )
        gaps.add("captured_object_unavailable")
    if missing_ref:
        freshness = _cap(
            freshness,
            LedgerFreshness.PARTIAL,
            LEDGER_FRESHNESS_ORDER,
        )
        gaps.add("missing_ref")
    if unknown_event:
        freshness = _cap(
            freshness,
            LedgerFreshness.PARTIAL,
            LEDGER_FRESHNESS_ORDER,
        )
        gaps.add("unknown_event")
    gaps.update(evidence_provenance_gaps)
    if len(gaps) > MAX_REF_LIST:
        raise _invalid_case()

    return Coverage(
        publication_channels=base.publication_channels,
        authorship_assurance=base.authorship_assurance,
        artifact_observation=observation,
        evidence_immutability=immutability,
        ledger_freshness=freshness,
        check_types=base.check_types,
        known_gaps=_sorted_unique(gaps),
    )


def _add_gap(
    gaps: dict[str, CaseGap],
    marker: str,
    code: str,
    refs: tuple[PublicSubjectRef, ...],
) -> None:
    gap = CaseGap(marker, code, refs)
    previous = gaps.get(marker)
    if previous is not None and previous != gap:
        raise _invalid_case()
    gaps[marker] = gap


def _projection_gap(
    marker: str,
    index: ReplayIndex,
) -> CaseGap:
    if marker.startswith("unknown_event:"):
        parts = marker.split(":", 2)
        if len(parts) != 3:
            raise _invalid_case()
        return CaseGap(marker, "unknown_event", (event_id(parts[1]),))
    if marker.startswith("redacted_event:"):
        target = event_id(marker.removeprefix("redacted_event:"))
        return CaseGap(marker, "redacted_event", (target,))
    if marker.startswith("redacted_object:"):
        target = object_id(marker.removeprefix("redacted_object:"))
        root = index.redaction_root_by_object.get(target)
        if root is None:
            raise _invalid_case()
        return CaseGap(marker, "redacted_object", (root,))
    if marker.startswith("missing_ref:"):
        parts = marker.split(":", 2)
        if len(parts) != 3:
            raise _invalid_case()
        return CaseGap(marker, "missing_ref", (event_id(parts[1]),))
    raise _invalid_case()


def _build_replay_index(records: tuple[LedgerRecord, ...]) -> ReplayIndex:
    index = empty_replay_index()
    for record in records:
        index = extend_replay_index(index, record)
    return index


def _validate_availability(
    projection: ProjectionState,
    records_by_event: Mapping[EventId, LedgerRecord],
    index: ReplayIndex,
    redacted_events: frozenset[EventId],
    redacted_objects: frozenset[ObjectId],
    availability: CaseAvailabilityFacts,
) -> None:
    expected_unavailable: set[EventId] = set()
    for record in _projection_records(projection):
        if record.payload is not None:
            continue
        envelope = records_by_event.get(record.source_event_id)
        if type(envelope) is not AcceptedEvent:
            raise _invalid_case()
        accepted = envelope
        recorded_redaction = record.source_event_id in redacted_events or accepted.redaction in {
            RedactionState.LOGICALLY_REDACTED,
            RedactionState.ERASED_CLAIMED,
        }
        if recorded_redaction:
            continue
        if accepted.redaction not in {RedactionState.PRESENT, RedactionState.KEY_UNAVAILABLE}:
            raise _invalid_case()
        expected_unavailable.add(record.source_event_id)
    if availability.unavailable_event_ids != _sorted_unique(expected_unavailable):
        raise _invalid_case()

    for unavailable in availability.unavailable_captured_objects:
        if (
            unavailable.object_id in redacted_objects
            or unavailable.object_id in index.redaction_root_by_object
        ):
            raise _invalid_case()
        associations = index.evidence_sources_by_object.get(unavailable.object_id, ())
        matched = tuple(
            item for item in associations if item.source_event_id == unavailable.source_event_id
        )
        if len(matched) != 1:
            raise _invalid_case()
        association = matched[0]
        current = projection.evidence.get(association.evidence_id)
        if (
            current is None
            or current.source_event_id != unavailable.source_event_id
            or current.payload is None
            or current.payload.captured_object_id != unavailable.object_id
            or not current.object_available
            or current.redacted_object_id is not None
        ):
            raise _invalid_case()


def completion_scope_gap(projection: ProjectionState) -> CaseGap | None:
    """Return the one plan-bound completion-scope gap for a readable case, if applicable."""

    completion_claim_present = any(
        record.payload is not None and record.payload.claim_kind is ClaimKind.COMPLETION
        for record in projection.claims.values()
    )
    plan_scope = current_plan_scope(projection.plans, projection.coverage_gaps)
    if not (
        completion_claim_present
        and plan_scope.has_plan
        and plan_scope.readable
        and plan_scope.declared_obligation_count == 0
    ):
        return None
    code = (
        COMPLETION_SCOPE_UNDECLARED_GAP
        if plan_scope.no_obligations_reason is None
        else COMPLETION_SCOPE_DECLARED_NONE_GAP
    )
    source_event = plan_scope.current_plan_event_id
    if source_event is None:  # pragma: no cover - readable plan state owns its source event
        raise _invalid_case()
    # Keep the marker bound to the declaration event for deterministic identity, but do not make
    # the plan event a finding subject. This is a coverage limitation, not ledger-staleness and
    # deliberately does not create a FindingKind.
    return CaseGap(f"{code}:{source_event}", code, ())


def build_deterministic_case(
    projection: ProjectionState,
    records: Iterable[LedgerRecord],
    availability: CaseAvailabilityFacts,
) -> DeterministicCase:
    """Freeze one exact accepted prefix into the pure deterministic-policy input."""

    if type(projection) is not ProjectionState or type(availability) is not CaseAvailabilityFacts:
        raise _invalid_case()
    accepted_prefix = tuple(records)
    if any(type(record) not in {AcceptedEvent, UnknownEvent} for record in accepted_prefix):
        raise _invalid_case()
    rebuilt = replay(accepted_prefix)
    if rebuilt != projection:
        raise _invalid_case()
    index = _build_replay_index(accepted_prefix)
    if index.frontier != projection.frontier or index.head_digest != projection.head_digest:
        raise _invalid_case()
    records_by_event = {record.event_id: record for record in accepted_prefix}
    if len(records_by_event) != len(accepted_prefix):
        raise _invalid_case()

    projection_redacted_events: set[EventId] = set()
    redacted_objects: set[ObjectId] = set()
    missing_sources: set[EventId] = set()
    unknown_events: set[EventId] = set()
    gaps: dict[str, CaseGap] = {}
    for marker in projection.coverage_gaps:
        gap = _projection_gap(marker, index)
        _add_gap(gaps, gap.marker, gap.code, gap.subject_refs)
        if gap.code == "redacted_event":
            projection_redacted_events.add(event_id(gap.subject_refs[0]))
        elif gap.code == "redacted_object":
            redacted_objects.add(object_id(marker.removeprefix("redacted_object:")))
        elif gap.code == "missing_ref":
            missing_sources.add(event_id(gap.subject_refs[0]))
        elif gap.code == "unknown_event":
            unknown_events.add(event_id(gap.subject_refs[0]))

    scope_gap = completion_scope_gap(projection)
    if scope_gap is not None:
        _add_gap(
            gaps,
            scope_gap.marker,
            scope_gap.code,
            scope_gap.subject_refs,
        )

    relevant_evidence: set[EvidenceId] = set()
    relevant_results: set[ResultId] = set()
    for record in projection.claims.values():
        if record.payload is not None:
            relevant_evidence.update(
                evidence_id(ref) for ref in record.payload.supporting_refs if ref.startswith("evd_")
            )
            relevant_results.update(
                result_id(ref) for ref in record.payload.supporting_refs if ref.startswith("res_")
            )
    for record in projection.obligations.values():
        if record.payload is not None:
            relevant_evidence.update(
                evidence_id(ref)
                for ref in record.payload.resolution_evidence_refs
                if ref.startswith("evd_")
            )
            relevant_results.update(
                result_id(ref)
                for ref in record.payload.resolution_evidence_refs
                if ref.startswith("res_")
            )
    for record in projection.responses.values():
        if record.payload is not None:
            relevant_evidence.update(
                evidence_id(ref) for ref in record.payload.evidence_refs if ref.startswith("evd_")
            )
            relevant_results.update(
                result_id(ref) for ref in record.payload.evidence_refs if ref.startswith("res_")
            )
    for result_ref in relevant_results:
        result_record = projection.results.get(result_ref)
        if result_record is not None and result_record.payload is not None:
            relevant_evidence.update(result_record.payload.evidence_refs)
    for evidence_ref in sorted(relevant_evidence, key=lambda value: str(value).encode("ascii")):
        record = projection.evidence.get(evidence_ref)
        if record is None or record.payload is None:
            continue
        payload = record.payload
        if payload.content_digest is None:
            continue
        if payload.digest_binding is None:
            code = "evidence_digest_subject_legacy_unknown"
        elif payload.digest_binding.content_availability is EvidenceContentAvailability.DIGEST_ONLY:
            code = "evidence_content_digest_only"
        elif payload.digest_binding.content_availability is EvidenceContentAvailability.WITHHELD:
            code = "evidence_content_withheld"
        else:
            continue
        marker = f"{code}:{record.source_event_id}"
        _add_gap(gaps, marker, code, (record.source_event_id,))

    current_sources = {record.source_event_id for record in _projection_records(projection)}
    envelope_redacted_events: set[EventId] = set()
    for source_event in current_sources:
        envelope = records_by_event.get(source_event)
        if type(envelope) is not AcceptedEvent:
            raise _invalid_case()
        accepted = envelope
        if accepted.redaction in {
            RedactionState.LOGICALLY_REDACTED,
            RedactionState.ERASED_CLAIMED,
        }:
            envelope_redacted_events.add(source_event)
            marker = f"redacted_event:{source_event}"
            _add_gap(gaps, marker, "redacted_event", (source_event,))
    redacted_events = frozenset(projection_redacted_events | envelope_redacted_events)

    _validate_availability(
        projection,
        records_by_event,
        index,
        redacted_events,
        frozenset(redacted_objects),
        availability,
    )
    unavailable_events = frozenset(availability.unavailable_event_ids)
    unavailable_objects = {
        (item.source_event_id, item.object_id) for item in availability.unavailable_captured_objects
    }
    for source_event in availability.unavailable_event_ids:
        _add_gap(
            gaps,
            f"unavailable_event:{source_event}",
            "event_payload_unavailable",
            (source_event,),
        )
    for item in availability.unavailable_captured_objects:
        _add_gap(
            gaps,
            f"unavailable_captured_object:{item.source_event_id}:{item.object_id}",
            "captured_object_unavailable",
            (item.source_event_id,),
        )

    for record in accepted_prefix:
        for known_gap in record.coverage.known_gaps:
            if known_gap not in gaps:
                _add_gap(gaps, known_gap, known_gap, ())

    material_records = tuple(
        record
        for record in accepted_prefix
        if type(record) is AcceptedEvent and record.schema.name in _SEMANTIC_HISTORY_FAMILIES
    )
    history_omitted_before_count = max(0, len(material_records) - MAX_FROZEN_HISTORY_EVENTS)
    retained_records = material_records[history_omitted_before_count:]
    history_bytes_remaining = MAX_FROZEN_HISTORY_BYTES
    newest_first: list[FrozenHistoryEvent] = []
    for record in reversed(retained_records):
        history_payload: JsonValue | None = None
        if record.redaction is not RedactionState.PRESENT:
            visibility: Literal[
                "available", "not_recorded", "not_selected", "redacted_never_send"
            ] = "redacted_never_send"
        elif record.event_id in unavailable_events or record.payload is None:
            visibility = "not_recorded"
        else:
            encoded_payload = encode_payload(record.payload)
            payload_bytes = len(canonical_encode(encoded_payload))
            if payload_bytes > history_bytes_remaining:
                visibility = "not_selected"
            else:
                visibility = "available"
                history_payload = encoded_payload
                history_bytes_remaining -= payload_bytes
        newest_first.append(
            FrozenHistoryEvent(
                event_id=record.event_id,
                schema_name=record.schema.name,
                schema_version=record.schema.version,
                ingestion_sequence=record.ledger.ingestion_sequence,
                occurred_at=record.occurred_at.wire,
                payload_digest=record.projection_locator.canonical_payload_digest,
                content_visibility=visibility,
                payload=history_payload,
            )
        )
    history = tuple(reversed(newest_first))

    source_by_ref = _logical_sources(projection)
    cited_event_absent = False

    def add_event_ref(ref: EventId) -> None:
        if ref not in records_by_event:
            raise _invalid_case()
        source_by_ref[ref] = ref

    def add_optional_event_ref(ref: EventId) -> None:
        """Bind a cited event when it is in the prefix; otherwise note a bounded gap.

        Projection source events and the selected history window remain required. Finding
        and contradiction citations can name an event that was never appended — observation
        advice historically computed those IDs from envelopes. Receipts admit at most 64
        gaps, so many dangling citations collapse to one task-global missing_ref rather
        than one marker per finding.
        """

        nonlocal cited_event_absent
        if ref in records_by_event:
            source_by_ref[ref] = ref
            return
        cited_event_absent = True

    for record in _projection_records(projection):
        add_event_ref(record.source_event_id)
    for item in history:
        add_event_ref(item.event_id)
    if projection.latest_tested_state is not None:
        add_event_ref(projection.latest_tested_state.source_check_event_id)
    for contradiction in projection.contradictions.values():
        if contradiction.disputed_ref.startswith("evt_"):
            add_optional_event_ref(event_id(contradiction.disputed_ref))
    for record in projection.findings.values():
        if record.payload is not None:
            for ref in record.payload.subject_refs:
                if ref.startswith("evt_"):
                    add_optional_event_ref(event_id(ref))
    for gap in gaps.values():
        for ref in gap.subject_refs:
            if not ref.startswith("evt_"):
                continue
            cited = event_id(ref)
            if cited in records_by_event:
                source_by_ref[cited] = cited
    if cited_event_absent and not any(gap.code == "missing_ref" for gap in gaps.values()):
        _add_gap(gaps, "missing_ref:cited_event_absent", "missing_ref", ())

    redacted_object_by_evidence: dict[EvidenceId, set[ObjectId]] = {}
    for target in redacted_objects:
        for association in index.evidence_sources_by_object.get(target, ()):
            current = projection.evidence.get(association.evidence_id)
            if current is not None and current.source_event_id == association.source_event_id:
                redacted_object_by_evidence.setdefault(association.evidence_id, set()).add(target)
    unavailable_object_by_evidence: dict[EvidenceId, set[ObjectId]] = {}
    for source_event, target in unavailable_objects:
        for association in index.evidence_sources_by_object.get(target, ()):
            if association.source_event_id == source_event:
                unavailable_object_by_evidence.setdefault(association.evidence_id, set()).add(
                    target
                )

    gap_codes_by_root: dict[EventId, set[str]] = {}
    for gap in gaps.values():
        for root in gap.subject_refs:
            if root.startswith("evt_"):
                gap_codes_by_root.setdefault(event_id(root), set()).add(gap.code)

    coverage_by_ref: dict[FindingBasisRef, Coverage] = {}
    for ref, source_event in source_by_ref.items():
        source = records_by_event.get(source_event)
        if source is None:
            raise _invalid_case()
        coverage_by_ref[ref] = _weaken_ref_coverage(
            source.coverage,
            redacted_event=source_event in redacted_events,
            unavailable_event=source_event in unavailable_events,
            redacted_object=(
                (ref.startswith("evd_") and evidence_id(ref) in redacted_object_by_evidence)
                or "redacted_object" in gap_codes_by_root.get(source_event, set())
            ),
            unavailable_object=(
                (ref.startswith("evd_") and evidence_id(ref) in unavailable_object_by_evidence)
                or "captured_object_unavailable" in gap_codes_by_root.get(source_event, set())
            ),
            missing_ref=source_event in missing_sources,
            unknown_event=(
                ref.startswith("evt_")
                and event_id(ref) in unknown_events
                and type(source) is UnknownEvent
            ),
            evidence_provenance_gaps=frozenset(
                gap_codes_by_root.get(source_event, set())
                & {
                    "evidence_content_digest_only",
                    "evidence_content_withheld",
                    "evidence_digest_subject_legacy_unknown",
                }
            ),
        )

    return DeterministicCase(
        projection=projection,
        frontier=Frontier(projection.frontier, projection.head_digest),
        availability=availability,
        allowed_ids=frozenset(source_by_ref),
        coverage_by_ref=coverage_by_ref,
        gaps=tuple(
            sorted(
                gaps.values(),
                key=lambda gap: (
                    _ascii_key(gap.marker),
                    tuple(_ascii_key(ref) for ref in gap.subject_refs),
                ),
            )
        ),
        history=history,
        history_availability="available",
        history_omitted_before_count=history_omitted_before_count,
    )


def _source_event_for_ref(case: DeterministicCase, ref: FindingBasisRef) -> EventId | None:
    if ref not in case.allowed_ids:
        return None
    if ref.startswith("evt_"):
        return event_id(ref)
    logical_sources = _logical_sources(case.projection)
    return logical_sources.get(ref)


def policy_public_root(
    case: DeterministicCase,
    ref: FindingBasisRef,
) -> PublicSubjectRef:
    validated = _basis_ref(ref)
    if validated not in case.allowed_ids:
        raise _invalid_policy()
    if validated.startswith("evt_"):
        return event_id(validated)
    if validated.startswith("obl_"):
        return obligation_id(validated)
    if validated.startswith("clm_"):
        return claim_id(validated)
    source = _source_event_for_ref(case, validated)
    if source is None:
        raise _invalid_policy()
    return source


def policy_source_availability(
    case: DeterministicCase,
    refs: Iterable[FindingBasisRef],
) -> FrozenSourceAvailability:
    values = tuple(refs)
    if any(ref not in case.allowed_ids for ref in values):
        return FrozenSourceAvailability.NOT_RECORDED
    coverages = tuple(case.coverage_by_ref[ref] for ref in values)
    if any(
        {"redacted_event", "redacted_object"} & set(coverage.known_gaps) for coverage in coverages
    ):
        return FrozenSourceAvailability.REDACTED_AT_SOURCE
    if any(
        {"event_payload_unavailable", "captured_object_unavailable"} & set(coverage.known_gaps)
        for coverage in coverages
    ):
        return FrozenSourceAvailability.UNAVAILABLE_AT_FREEZE
    return FrozenSourceAvailability.AVAILABLE


def _derived_finding_coverage(
    case: DeterministicCase,
    supporting_refs: tuple[FindingBasisRef, ...],
) -> Coverage:
    if not supporting_refs:
        raise _invalid_policy()
    try:
        current = case.coverage_by_ref[supporting_refs[0]]
        for ref in supporting_refs[1:]:
            current = weakest(current, case.coverage_by_ref[ref])
    except KeyError as exc:
        raise _invalid_policy() from exc
    channels = tuple(
        sorted(
            set(current.publication_channels) | {PublicationChannel.ENGINE_DERIVED},
            key=lambda item: _ascii_key(item.value),
        )
    )
    check_types = set(current.check_types) | {CheckType.DETERMINISTIC}
    check_types.discard(CheckType.NONE)
    return Coverage(
        publication_channels=channels,
        authorship_assurance=current.authorship_assurance,
        artifact_observation=current.artifact_observation,
        evidence_immutability=current.evidence_immutability,
        ledger_freshness=current.ledger_freshness,
        check_types=tuple(sorted(check_types, key=lambda item: _ascii_key(item.value))),
        known_gaps=current.known_gaps,
    )


def build_policy_assessment(
    case: DeterministicCase,
    policy: PolicyPack,
    kind: FindingKind,
    subject_refs: tuple[FindingBasisRef, ...],
    observed_facts: tuple[FindingFact, ...],
    required_but_missing_facts: tuple[FindingFact, ...] = (),
    *,
    subject_state_relation: SubjectStateRelation = SubjectStateRelation.UNKNOWN,
    source_availability: FrozenSourceAvailability = FrozenSourceAvailability.AVAILABLE,
) -> DeterministicAssessment:
    public_refs = cast(
        tuple[PublicSubjectRef, ...],
        _validated_ref_tuple(subject_refs, public_only=True),
    )
    if any(ref not in case.allowed_ids for ref in public_refs):
        raise _invalid_policy()
    ordered_observed = tuple(sorted(observed_facts, key=_fact_key))
    ordered_missing = tuple(sorted(required_but_missing_facts, key=_fact_key))
    supporting_refs = _sorted_unique(ref for fact in ordered_observed for ref in fact.subject_refs)
    coverage = _derived_finding_coverage(case, supporting_refs)
    basis = FindingBasis(
        rule_id=f"{policy.policy_id}/{kind.value}",
        observed_facts=ordered_observed,
        required_but_missing_facts=ordered_missing,
        subject_state_relation=subject_state_relation,
        source_availability=source_availability,
        coverage_gaps=coverage.known_gaps,
        supporting_refs=supporting_refs,
    )
    summary, detail = render_deterministic_finding_text(kind, public_refs, basis.coverage_gaps)
    priority, _ = FINDING_KIND_TRAITS[kind]
    candidate = CandidateFinding(
        kind=kind,
        origin=FindingOrigin.DETERMINISTIC,
        priority=priority,
        summary=summary,
        detail=detail,
        subject_refs=public_refs,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        subject_frontier=case.frontier,
        coverage=coverage,
        provenance=None,
    )
    return DeterministicAssessment(candidate, basis)


def run_deterministic_policies(
    case: DeterministicCase,
    policy: PolicyPack,
) -> DeterministicPolicyResult:
    """Run one closed built-in policy pack over an immutable deterministic case."""

    if type(case) is not DeterministicCase or type(policy) is not PolicyPack:
        raise _invalid_policy()
    if (policy.policy_id, policy.policy_version) == ("work-integrity", "0.1.0"):
        from yoetz.kernel.policies.work_integrity import (
            WORK_INTEGRITY_FACT_CODES,
            WORK_INTEGRITY_POLICY_ID,
            WORK_INTEGRITY_POLICY_PACK,
            WORK_INTEGRITY_POLICY_VERSION,
            work_integrity_findings,
        )

        if (
            WORK_INTEGRITY_POLICY_ID != policy.policy_id
            or WORK_INTEGRITY_POLICY_VERSION != policy.policy_version
            or WORK_INTEGRITY_POLICY_PACK != policy
        ):
            raise _invalid_policy()
        facts = WORK_INTEGRITY_FACT_CODES
        rule_order: tuple[FindingKind, ...] = (
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
        assessments = work_integrity_findings(case)
    elif (policy.policy_id, policy.policy_version) == ("research-evidence", "0.1.0"):
        from yoetz.kernel.policies.research_evidence import (
            RESEARCH_EVIDENCE_FACT_CODES,
            RESEARCH_EVIDENCE_POLICY_ID,
            RESEARCH_EVIDENCE_POLICY_PACK,
            RESEARCH_EVIDENCE_POLICY_VERSION,
            research_evidence_findings,
        )

        if (
            RESEARCH_EVIDENCE_POLICY_ID != policy.policy_id
            or RESEARCH_EVIDENCE_POLICY_VERSION != policy.policy_version
            or RESEARCH_EVIDENCE_POLICY_PACK != policy
        ):
            raise _invalid_policy()
        facts = RESEARCH_EVIDENCE_FACT_CODES
        rule_order = (
            FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
            FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
            FindingKind.MATERIAL_LIMITATION_OMITTED,
            FindingKind.QUESTIONABLE_FINDING_REJECTION,
        )
        assessments = research_evidence_findings(case)
    else:
        raise _invalid_policy()

    if type(assessments) is not tuple:
        raise _invalid_policy()
    ordinal = {kind: index for index, kind in enumerate(rule_order)}
    validated: list[DeterministicAssessment] = []
    seen: set[tuple[str, str, tuple[PublicSubjectRef, ...]]] = set()
    for assessment in assessments:
        if type(assessment) is not DeterministicAssessment:
            raise _invalid_policy()
        candidate = assessment.candidate
        basis = assessment.basis
        if (
            candidate.policy_id != policy.policy_id
            or candidate.policy_version != policy.policy_version
            or candidate.kind not in ordinal
            or any(
                fact.fact_code not in facts
                for fact in (*basis.observed_facts, *basis.required_but_missing_facts)
            )
            or any(ref not in case.allowed_ids for ref in basis.supporting_refs)
            or any(ref not in case.allowed_ids for ref in candidate.subject_refs)
            or candidate.coverage != _derived_finding_coverage(case, basis.supporting_refs)
        ):
            raise _invalid_policy()
        key = (candidate.policy_id, basis.rule_id, candidate.subject_refs)
        if key in seen:
            raise _invalid_policy()
        seen.add(key)
        validated.append(assessment)
    ordered = tuple(
        sorted(
            validated,
            key=lambda item: (
                ordinal[item.candidate.kind],
                tuple(_ascii_key(ref) for ref in item.candidate.subject_refs),
            ),
        )
    )
    return DeterministicPolicyResult(ordered)
