"""Canonical finding values, finalized semantic provenance, and ranking inputs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final, cast

from yoetz.domain.values import (
    ClaimId,
    EventId,
    FindingId,
    Frontier,
    JsonObject,
    JsonValue,
    ObligationId,
    claim_id,
    event_id,
    finding_id,
    frontier_from_json,
    obligation_id,
    parse_wire_sequence,
    render_wire_sequence,
    validate_commitment,
    validate_sha256_digest,
)
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.canonical import ensure_canonical_set, ensure_canonical_value
from yoetz.protocol.coverage import (
    ARTIFACT_OBSERVATION_ORDER,
    AUTHORSHIP_ASSURANCE_ORDER,
    EVIDENCE_IMMUTABILITY_ORDER,
    LEDGER_FRESHNESS_ORDER,
    CheckType,
    Coverage,
    coverage_from_json,
    coverage_to_json,
)
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.protocol.models import (
    SemanticReason,
    SemanticStatus,
    validate_semantic_outcome,
)

__all__ = [
    "FINDING_KIND_TRAITS",
    "CandidateFinding",
    "CheckVerdict",
    "CostFields",
    "DeterministicFinding",
    "Finding",
    "FindingKind",
    "FindingOrigin",
    "RankedFindings",
    "ResponseDisposition",
    "SamplingParams",
    "SemanticDispatchKind",
    "SemanticFailureClass",
    "SemanticFinding",
    "SemanticProvenance",
    "TokenUsage",
    "WaiverScope",
    "finding_from_json",
    "finding_to_json",
    "rank_key",
    "semantic_provenance_from_json",
    "semantic_provenance_to_json",
]

_MAX_SAFE_INTEGER: Final = 2**53 - 1
_MAX_FINDING_TEXT_LENGTH: Final = 8_192
_MAX_SUBJECT_REFS: Final = 64

_IDENTITY_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.ASCII)
_MODEL_IDENTITY_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$", re.ASCII)
_VERSION_IDENTITY_PATTERN: Final = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$", re.ASCII)
_PROVIDER_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)
_CURRENCY_PATTERN: Final = re.compile(r"^[A-Z]{3}$", re.ASCII)
_FIXED_DECIMAL_PATTERN: Final = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$", re.ASCII)
_SEMANTIC_VERSION_PATTERN: Final = re.compile(
    r"^(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$",
    re.ASCII,
)


class FindingKind(str, Enum):  # noqa: UP042 - exact wire enum base
    ACTION_WITHOUT_RESULT = "action_without_result"
    CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE = "claim_without_admissible_evidence"
    COMPLETION_WITH_OPEN_OBLIGATIONS = "completion_with_open_obligations"
    CONTRADICTORY_CLAIMS_UNRESOLVED = "contradictory_claims_unresolved"
    DIFF_DOES_NOT_MATCH_ACCOUNT = "diff_does_not_match_account"
    EVIDENCE_DOES_NOT_SUPPORT_CLAIM = "evidence_does_not_support_claim"
    FAILED_WORK_OMITTED = "failed_work_omitted"
    LEDGER_STALE_OR_INCOMPLETE = "ledger_stale_or_incomplete"
    MATERIAL_LIMITATION_OMITTED = "material_limitation_omitted"
    QUESTIONABLE_FINDING_REJECTION = "questionable_finding_rejection"
    REQUESTED_ITEM_NEVER_ATTEMPTED = "requested_item_never_attempted"
    RESULT_WITHOUT_ACTION = "result_without_action"
    STALE_EVIDENCE_FOR_CHANGED_STATE = "stale_evidence_for_changed_state"
    WEAK_OR_STALE_RESPONSE = "weak_or_stale_response"


class FindingOrigin(str, Enum):  # noqa: UP042 - exact wire enum base
    DETERMINISTIC = "deterministic"
    SEMANTIC_MODEL_DERIVED = "semantic_model_derived"


class CheckVerdict(str, Enum):  # noqa: UP042 - exact wire enum base
    ACTION_REQUIRED = "action_required"
    NO_ISSUE_DETECTED = "no_issue_detected"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    INCOMPLETE_CHECK = "incomplete_check"


class WaiverScope(str, Enum):  # noqa: UP042 - exact wire enum base
    FINDING_ONLY = "finding_only"


class ResponseDisposition(str, Enum):  # noqa: UP042 - exact wire enum base
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    WAIVED = "waived"


class SemanticDispatchKind(str, Enum):  # noqa: UP042 - exact wire enum base
    EXTERNAL = "external"
    LOCAL_MODEL = "local_model"


class SemanticFailureClass(str, Enum):  # noqa: UP042 - exact wire enum base
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    PROVIDER_OUTAGE = "provider_outage"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    RESPONSE_SCHEMA = "response_schema"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    UNSUPPORTED_PROFILE = "unsupported_profile"


FINDING_KIND_TRAITS: Final[MappingProxyType[FindingKind, tuple[int, bool]]] = MappingProxyType(
    {
        FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS: (1, True),
        FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED: (2, True),
        FindingKind.FAILED_WORK_OMITTED: (1, True),
        FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE: (1, True),
        FindingKind.RESULT_WITHOUT_ACTION: (2, True),
        FindingKind.ACTION_WITHOUT_RESULT: (3, True),
        FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE: (2, True),
        FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED: (1, True),
        FindingKind.LEDGER_STALE_OR_INCOMPLETE: (3, False),
        FindingKind.WEAK_OR_STALE_RESPONSE: (2, True),
        FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM: (1, True),
        FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT: (1, True),
        FindingKind.MATERIAL_LIMITATION_OMITTED: (1, True),
        FindingKind.QUESTIONABLE_FINDING_REJECTION: (2, True),
    }
)

_WORK_INTEGRITY_KINDS: Final = frozenset(
    {
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
    }
)
_POLICY_IDENTITY_BY_KIND: Final[MappingProxyType[FindingKind, tuple[str, str]]] = MappingProxyType(
    {
        kind: (
            ("work-integrity", "0.1.0")
            if kind in _WORK_INTEGRITY_KINDS
            else ("research-evidence", "0.1.0")
        )
        for kind in FindingKind
    }
)

_TERMINAL_SEMANTIC_STATUSES: Final = frozenset(
    {
        SemanticStatus.SUCCEEDED,
        SemanticStatus.REFUSED,
        SemanticStatus.TIMEOUT,
        SemanticStatus.INVALID,
        SemanticStatus.UNAVAILABLE,
        SemanticStatus.LATE,
        SemanticStatus.STALE,
        SemanticStatus.FAILED,
    }
)


def _valid_uint53(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SAFE_INTEGER


def _validate_bounded_text(value: object) -> None:
    if type(value) is not str or not 1 <= len(value) <= _MAX_FINDING_TEXT_LENGTH:
        raise ProtocolValueError("finding_json_shape_invalid")
    ensure_canonical_value(value)


def _valid_pattern(value: object, pattern: re.Pattern[str], maximum: int, minimum: int = 1) -> bool:
    return (
        type(value) is str
        and minimum <= len(value) <= maximum
        and pattern.fullmatch(value) is not None
    )


def _snapshot_validated_id(kind: IdKind, value: object) -> str:
    validated = validate_id(kind, value)
    return str.__getitem__(validated, slice(None))


def _validate_subject_refs(
    value: object,
) -> tuple[EventId | ObligationId | ClaimId, ...]:
    if type(value) is not tuple:
        raise ProtocolValueError("invalid_finding_subject_refs")
    refs = cast(tuple[object, ...], value)
    if not 1 <= len(refs) <= _MAX_SUBJECT_REFS:
        raise ProtocolValueError("invalid_finding_subject_refs")
    ensure_canonical_set(cast(tuple[str, ...], refs))
    validated: list[EventId | ObligationId | ClaimId] = []
    for ref in refs:
        candidate = cast(str, ref)
        prefix = candidate[:4]
        if prefix == "evt_":
            validated.append(event_id(candidate))
        elif prefix == "obl_":
            validated.append(obligation_id(candidate))
        elif prefix == "clm_":
            validated.append(claim_id(candidate))
        else:
            raise ProtocolValueError("invalid_finding_subject_refs")
    return tuple(validated)


@dataclass(frozen=True, slots=True)
class SamplingParams:
    max_output_tokens: int
    temperature: str | None = None
    top_p: str | None = None
    seed: int | None = None

    def __post_init__(self) -> None:
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 8_192:
            raise ProtocolValueError("invalid_sampling_params")
        for value in (self.temperature, self.top_p):
            if value is not None and not _valid_pattern(value, _FIXED_DECIMAL_PATTERN, 32):
                raise ProtocolValueError("invalid_sampling_params")
        if self.seed is not None and not _valid_uint53(self.seed):
            raise ProtocolValueError("invalid_sampling_params")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        if not all(
            _valid_uint53(value)
            for value in (self.input_tokens, self.output_tokens, self.total_tokens)
        ):
            raise ProtocolValueError("invalid_token_usage")


@dataclass(frozen=True, slots=True)
class CostFields:
    currency: str
    input_microunits: int
    output_microunits: int
    total_microunits: int

    def __post_init__(self) -> None:
        if type(self.currency) is not str or _CURRENCY_PATTERN.fullmatch(self.currency) is None:
            raise ProtocolValueError("invalid_cost_fields")
        if not all(
            _valid_uint53(value)
            for value in (
                self.input_microunits,
                self.output_microunits,
                self.total_microunits,
            )
        ):
            raise ProtocolValueError("invalid_cost_fields")


@dataclass(frozen=True, slots=True)
class SemanticProvenance:
    provider: str
    endpoint_profile_id: str
    endpoint_profile_version: str
    model: str
    sdk_version: str
    prompt_digest: str
    schema_digest: str
    policy_digest: str
    privacy_policy_digest: str
    sampling_params: SamplingParams
    latency_ms: int
    semantic_attempt_id: str
    dispatch_kind: SemanticDispatchKind
    privacy_receipt_id: str
    status: SemanticStatus
    reason: SemanticReason
    provider_request_id: str | None = None
    token_usage: TokenUsage | None = None
    cost_fields: CostFields | None = None
    failure_class: SemanticFailureClass | None = None
    egress_authorization_id: str | None = None
    local_disclosure_reservation_id: str | None = None
    request_commitment: str | None = None

    def __post_init__(self) -> None:
        if not _valid_pattern(self.provider, _IDENTITY_PATTERN, 128):
            raise ProtocolValueError("invalid_semantic_provenance")
        if not _valid_pattern(self.endpoint_profile_id, _IDENTITY_PATTERN, 128):
            raise ProtocolValueError("invalid_semantic_provenance")
        if not _valid_pattern(
            self.endpoint_profile_version, _SEMANTIC_VERSION_PATTERN, 128, minimum=5
        ):
            raise ProtocolValueError("invalid_semantic_provenance")
        if not _valid_pattern(self.model, _MODEL_IDENTITY_PATTERN, 256):
            raise ProtocolValueError("invalid_semantic_provenance")
        if not _valid_pattern(self.sdk_version, _VERSION_IDENTITY_PATTERN, 128):
            raise ProtocolValueError("invalid_semantic_provenance")

        validate_sha256_digest(self.prompt_digest)
        validate_sha256_digest(self.schema_digest)
        validate_sha256_digest(self.policy_digest)
        validate_sha256_digest(self.privacy_policy_digest)
        if type(self.sampling_params) is not SamplingParams or not _valid_uint53(self.latency_ms):
            raise ProtocolValueError("invalid_semantic_provenance")

        object.__setattr__(
            self,
            "semantic_attempt_id",
            _snapshot_validated_id(IdKind.SEMANTIC_ATTEMPT, self.semantic_attempt_id),
        )
        if type(self.dispatch_kind) is not SemanticDispatchKind:
            raise ProtocolValueError("invalid_semantic_dispatch_kind")
        object.__setattr__(
            self,
            "privacy_receipt_id",
            _snapshot_validated_id(IdKind.EGRESS_RECEIPT, self.privacy_receipt_id),
        )

        validate_semantic_outcome(self.status, self.reason)
        if self.status not in _TERMINAL_SEMANTIC_STATUSES:
            raise ProtocolValueError("invalid_semantic_provenance")

        if self.provider_request_id is not None and not _valid_pattern(
            self.provider_request_id, _PROVIDER_REQUEST_ID_PATTERN, 256
        ):
            raise ProtocolValueError("invalid_semantic_provenance")
        if self.token_usage is not None and type(self.token_usage) is not TokenUsage:
            raise ProtocolValueError("invalid_semantic_provenance")
        if self.cost_fields is not None and type(self.cost_fields) is not CostFields:
            raise ProtocolValueError("invalid_semantic_provenance")
        if self.failure_class is not None and type(self.failure_class) is not SemanticFailureClass:
            raise ProtocolValueError("invalid_semantic_failure_class")

        if self.egress_authorization_id is not None:
            object.__setattr__(
                self,
                "egress_authorization_id",
                _snapshot_validated_id(IdKind.EGRESS_AUTHORIZATION, self.egress_authorization_id),
            )
        if self.local_disclosure_reservation_id is not None:
            object.__setattr__(
                self,
                "local_disclosure_reservation_id",
                _snapshot_validated_id(
                    IdKind.PRIVACY_PROPOSAL, self.local_disclosure_reservation_id
                ),
            )
        if self.request_commitment is not None:
            validate_commitment(self.request_commitment)

        if self.dispatch_kind is SemanticDispatchKind.EXTERNAL:
            if (
                self.egress_authorization_id is None
                or self.request_commitment is None
                or self.local_disclosure_reservation_id is not None
            ):
                raise ProtocolValueError("invalid_semantic_provenance")
        elif (
            self.local_disclosure_reservation_id is None
            or self.egress_authorization_id is not None
            or self.request_commitment is not None
        ):
            raise ProtocolValueError("invalid_semantic_provenance")


def _validate_finding_fields(
    *,
    kind: object,
    origin: object,
    priority: object,
    summary: object,
    detail: object,
    subject_refs: object,
    policy_id: object,
    policy_version: object,
    subject_frontier: object,
    coverage: object,
    provenance: object,
) -> tuple[EventId | ObligationId | ClaimId, ...]:
    if type(kind) is not FindingKind:
        raise ProtocolValueError("invalid_finding_kind")
    validated_kind = kind
    if type(origin) is not FindingOrigin:
        raise ProtocolValueError("invalid_finding_origin")
    validated_origin = origin
    required_priority, _ = FINDING_KIND_TRAITS[validated_kind]
    if type(priority) is not int or priority != required_priority:
        raise ProtocolValueError("finding_priority_mismatch")
    _validate_bounded_text(summary)
    _validate_bounded_text(detail)
    validated_refs = _validate_subject_refs(subject_refs)

    expected_policy_id, expected_policy_version = _POLICY_IDENTITY_BY_KIND[validated_kind]
    if (
        type(policy_id) is not str
        or type(policy_version) is not str
        or policy_id != expected_policy_id
        or policy_version != expected_policy_version
    ):
        raise ProtocolValueError("invalid_finding_policy_identity")
    if type(subject_frontier) is not Frontier:
        raise ProtocolValueError("invalid_frontier")
    if type(coverage) is not Coverage:
        raise ProtocolValueError("invalid_coverage_value")

    if validated_origin is FindingOrigin.DETERMINISTIC:
        if provenance is not None:
            raise ProtocolValueError("invalid_finding_provenance")
    elif (
        type(provenance) is not SemanticProvenance
        or provenance.status is not SemanticStatus.SUCCEEDED
        or provenance.reason is not SemanticReason.SEMANTIC_COMPLETED
    ):
        raise ProtocolValueError("invalid_finding_provenance")
    return validated_refs


@dataclass(frozen=True, slots=True)
class CandidateFinding:
    kind: FindingKind
    origin: FindingOrigin
    priority: int
    summary: str
    detail: str
    subject_refs: tuple[EventId | ObligationId | ClaimId, ...]
    policy_id: str
    policy_version: str
    subject_frontier: Frontier
    coverage: Coverage
    provenance: SemanticProvenance | None = None

    def __post_init__(self) -> None:
        validated_refs = _validate_finding_fields(
            kind=self.kind,
            origin=self.origin,
            priority=self.priority,
            summary=self.summary,
            detail=self.detail,
            subject_refs=self.subject_refs,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            subject_frontier=self.subject_frontier,
            coverage=self.coverage,
            provenance=self.provenance,
        )
        object.__setattr__(self, "subject_refs", validated_refs)


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: FindingId
    kind: FindingKind
    origin: FindingOrigin
    priority: int
    summary: str
    detail: str
    subject_refs: tuple[EventId | ObligationId | ClaimId, ...]
    policy_id: str
    policy_version: str
    subject_frontier: Frontier
    coverage: Coverage
    provenance: SemanticProvenance | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", finding_id(self.finding_id))
        validated_refs = _validate_finding_fields(
            kind=self.kind,
            origin=self.origin,
            priority=self.priority,
            summary=self.summary,
            detail=self.detail,
            subject_refs=self.subject_refs,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            subject_frontier=self.subject_frontier,
            coverage=self.coverage,
            provenance=self.provenance,
        )
        object.__setattr__(self, "subject_refs", validated_refs)


DeterministicFinding = Finding
SemanticFinding = Finding


@dataclass(frozen=True, slots=True)
class RankedFindings:
    findings: tuple[Finding, ...]
    suppressed_count: int
    verdict: CheckVerdict
    coverage: Coverage

    def __post_init__(self) -> None:
        if type(self.findings) is not tuple or any(
            type(finding) is not Finding for finding in self.findings
        ):
            raise ProtocolValueError("invalid_ranked_findings")
        ids = tuple(finding.finding_id for finding in self.findings)
        if len(ids) != len(set(ids)):
            raise ProtocolValueError("invalid_ranked_findings")
        if not _valid_uint53(self.suppressed_count):
            raise ProtocolValueError("invalid_ranked_findings")
        if type(self.verdict) is not CheckVerdict or type(self.coverage) is not Coverage:
            raise ProtocolValueError("invalid_ranked_findings")


def _require_json_object(
    value: JsonValue,
    *,
    required: frozenset[str],
    allowed: frozenset[str],
    reason: str,
) -> JsonObject:
    if type(value) is not JsonObject:
        raise ProtocolValueError(reason)
    source = value
    try:
        keys = frozenset(source)
    except Exception as exc:
        raise ProtocolValueError(reason) from exc
    if not required <= keys or not keys <= allowed:
        raise ProtocolValueError(reason)
    return source


def _field(source: Mapping[str, JsonValue], key: str, reason: str) -> JsonValue:
    try:
        return source[key]
    except Exception as exc:
        raise ProtocolValueError(reason) from exc


def _optional_field(source: Mapping[str, JsonValue], key: str) -> JsonValue | None:
    if key not in source:
        return None
    return source[key]


def _parse_uint53_wire(value: JsonValue, reason: str) -> int:
    parsed = parse_wire_sequence(cast(str, value))
    if parsed > _MAX_SAFE_INTEGER:
        raise ProtocolValueError(reason)
    return parsed


def _sampling_params_from_json(value: JsonValue) -> SamplingParams:
    required = frozenset({"max_output_tokens"})
    allowed = frozenset({"max_output_tokens", "temperature", "top_p", "seed"})
    source = _require_json_object(
        value,
        required=required,
        allowed=allowed,
        reason="semantic_provenance_json_shape_invalid",
    )
    if any(key in source and source[key] is None for key in allowed - required):
        raise ProtocolValueError("semantic_provenance_json_shape_invalid")
    temperature = _optional_field(source, "temperature")
    top_p = _optional_field(source, "top_p")
    seed = _optional_field(source, "seed")
    return SamplingParams(
        max_output_tokens=_parse_uint53_wire(
            _field(source, "max_output_tokens", "semantic_provenance_json_shape_invalid"),
            "invalid_sampling_params",
        ),
        temperature=cast(str | None, temperature),
        top_p=cast(str | None, top_p),
        seed=None if seed is None else _parse_uint53_wire(seed, "invalid_sampling_params"),
    )


def _token_usage_from_json(value: JsonValue) -> TokenUsage:
    keys = frozenset({"input_tokens", "output_tokens", "total_tokens"})
    source = _require_json_object(
        value,
        required=keys,
        allowed=keys,
        reason="semantic_provenance_json_shape_invalid",
    )
    return TokenUsage(
        input_tokens=_parse_uint53_wire(source["input_tokens"], "invalid_token_usage"),
        output_tokens=_parse_uint53_wire(source["output_tokens"], "invalid_token_usage"),
        total_tokens=_parse_uint53_wire(source["total_tokens"], "invalid_token_usage"),
    )


def _cost_fields_from_json(value: JsonValue) -> CostFields:
    keys = frozenset({"currency", "input_microunits", "output_microunits", "total_microunits"})
    source = _require_json_object(
        value,
        required=keys,
        allowed=keys,
        reason="semantic_provenance_json_shape_invalid",
    )
    return CostFields(
        currency=cast(str, source["currency"]),
        input_microunits=_parse_uint53_wire(source["input_microunits"], "invalid_cost_fields"),
        output_microunits=_parse_uint53_wire(source["output_microunits"], "invalid_cost_fields"),
        total_microunits=_parse_uint53_wire(source["total_microunits"], "invalid_cost_fields"),
    )


_PROVENANCE_REQUIRED_KEYS: Final = frozenset(
    {
        "provider",
        "endpoint_profile_id",
        "endpoint_profile_version",
        "model",
        "sdk_version",
        "prompt_digest",
        "schema_digest",
        "policy_digest",
        "privacy_policy_digest",
        "sampling_params",
        "latency_ms",
        "semantic_attempt_id",
        "dispatch_kind",
        "privacy_receipt_id",
        "status",
        "reason",
    }
)
_PROVENANCE_OPTIONAL_KEYS: Final = frozenset(
    {
        "provider_request_id",
        "token_usage",
        "cost_fields",
        "failure_class",
        "egress_authorization_id",
        "local_disclosure_reservation_id",
        "request_commitment",
    }
)


def semantic_provenance_from_json(value: JsonValue) -> SemanticProvenance:
    """Decode one exact finalized semantic-provenance object."""

    source = _require_json_object(
        value,
        required=_PROVENANCE_REQUIRED_KEYS,
        allowed=_PROVENANCE_REQUIRED_KEYS | _PROVENANCE_OPTIONAL_KEYS,
        reason="semantic_provenance_json_shape_invalid",
    )
    if any(key in source and source[key] is None for key in _PROVENANCE_OPTIONAL_KEYS):
        raise ProtocolValueError("semantic_provenance_json_shape_invalid")
    dispatch_wire = source["dispatch_kind"]
    failure_wire = _optional_field(source, "failure_class")
    status_wire = source["status"]
    reason_wire = source["reason"]
    try:
        dispatch_kind = SemanticDispatchKind(dispatch_wire)
    except (TypeError, ValueError) as exc:
        raise ProtocolValueError("invalid_semantic_dispatch_kind") from exc
    try:
        status = SemanticStatus(status_wire)
        semantic_reason = SemanticReason(reason_wire)
    except (TypeError, ValueError) as exc:
        raise ProtocolValueError("invalid_semantic_provenance") from exc
    if failure_wire is None:
        failure_class = None
    else:
        try:
            failure_class = SemanticFailureClass(failure_wire)
        except (TypeError, ValueError) as exc:
            raise ProtocolValueError("invalid_semantic_failure_class") from exc

    token_usage_value = _optional_field(source, "token_usage")
    cost_fields_value = _optional_field(source, "cost_fields")
    return SemanticProvenance(
        provider=cast(str, source["provider"]),
        endpoint_profile_id=cast(str, source["endpoint_profile_id"]),
        endpoint_profile_version=cast(str, source["endpoint_profile_version"]),
        model=cast(str, source["model"]),
        sdk_version=cast(str, source["sdk_version"]),
        prompt_digest=cast(str, source["prompt_digest"]),
        schema_digest=cast(str, source["schema_digest"]),
        policy_digest=cast(str, source["policy_digest"]),
        privacy_policy_digest=cast(str, source["privacy_policy_digest"]),
        sampling_params=_sampling_params_from_json(source["sampling_params"]),
        latency_ms=_parse_uint53_wire(source["latency_ms"], "invalid_semantic_provenance"),
        semantic_attempt_id=cast(str, source["semantic_attempt_id"]),
        dispatch_kind=dispatch_kind,
        privacy_receipt_id=cast(str, source["privacy_receipt_id"]),
        status=status,
        reason=semantic_reason,
        provider_request_id=cast(str | None, _optional_field(source, "provider_request_id")),
        token_usage=(
            None if token_usage_value is None else _token_usage_from_json(token_usage_value)
        ),
        cost_fields=(
            None if cost_fields_value is None else _cost_fields_from_json(cost_fields_value)
        ),
        failure_class=failure_class,
        egress_authorization_id=cast(
            str | None, _optional_field(source, "egress_authorization_id")
        ),
        local_disclosure_reservation_id=cast(
            str | None, _optional_field(source, "local_disclosure_reservation_id")
        ),
        request_commitment=cast(str | None, _optional_field(source, "request_commitment")),
    )


def semantic_provenance_to_json(value: SemanticProvenance) -> JsonObject:
    """Encode finalized semantic provenance into its one closed wire object."""

    if type(value) is not SemanticProvenance:
        raise ProtocolValueError("invalid_semantic_provenance")
    sampling: dict[str, JsonValue] = {
        "max_output_tokens": render_wire_sequence(value.sampling_params.max_output_tokens)
    }
    if value.sampling_params.temperature is not None:
        sampling["temperature"] = value.sampling_params.temperature
    if value.sampling_params.top_p is not None:
        sampling["top_p"] = value.sampling_params.top_p
    if value.sampling_params.seed is not None:
        sampling["seed"] = render_wire_sequence(value.sampling_params.seed)

    result: dict[str, JsonValue] = {
        "provider": value.provider,
        "endpoint_profile_id": value.endpoint_profile_id,
        "endpoint_profile_version": value.endpoint_profile_version,
        "model": value.model,
        "sdk_version": value.sdk_version,
        "prompt_digest": value.prompt_digest,
        "schema_digest": value.schema_digest,
        "policy_digest": value.policy_digest,
        "privacy_policy_digest": value.privacy_policy_digest,
        "sampling_params": JsonObject(sampling),
        "latency_ms": render_wire_sequence(value.latency_ms),
        "semantic_attempt_id": value.semantic_attempt_id,
        "dispatch_kind": value.dispatch_kind.value,
        "privacy_receipt_id": value.privacy_receipt_id,
        "status": value.status.value,
        "reason": value.reason.value,
    }
    if value.provider_request_id is not None:
        result["provider_request_id"] = value.provider_request_id
    if value.token_usage is not None:
        result["token_usage"] = JsonObject(
            {
                "input_tokens": render_wire_sequence(value.token_usage.input_tokens),
                "output_tokens": render_wire_sequence(value.token_usage.output_tokens),
                "total_tokens": render_wire_sequence(value.token_usage.total_tokens),
            }
        )
    if value.cost_fields is not None:
        result["cost_fields"] = JsonObject(
            {
                "currency": value.cost_fields.currency,
                "input_microunits": render_wire_sequence(value.cost_fields.input_microunits),
                "output_microunits": render_wire_sequence(value.cost_fields.output_microunits),
                "total_microunits": render_wire_sequence(value.cost_fields.total_microunits),
            }
        )
    if value.failure_class is not None:
        result["failure_class"] = value.failure_class.value
    if value.egress_authorization_id is not None:
        result["egress_authorization_id"] = value.egress_authorization_id
    if value.local_disclosure_reservation_id is not None:
        result["local_disclosure_reservation_id"] = value.local_disclosure_reservation_id
    if value.request_commitment is not None:
        result["request_commitment"] = value.request_commitment
    return JsonObject(result)


_FINDING_REQUIRED_KEYS: Final = frozenset(
    {
        "finding_id",
        "kind",
        "origin",
        "priority",
        "summary",
        "detail",
        "subject_refs",
        "policy_id",
        "policy_version",
        "subject_frontier",
        "coverage",
    }
)
_FINDING_ALLOWED_KEYS: Final = _FINDING_REQUIRED_KEYS | {"provenance"}


def finding_from_json(value: JsonValue) -> Finding:
    """Decode one exact finding-schema object."""

    source = _require_json_object(
        value,
        required=_FINDING_REQUIRED_KEYS,
        allowed=_FINDING_ALLOWED_KEYS,
        reason="finding_json_shape_invalid",
    )
    if "provenance" in source and source["provenance"] is None:
        raise ProtocolValueError("finding_json_shape_invalid")
    try:
        kind = FindingKind(source["kind"])
    except (TypeError, ValueError) as exc:
        raise ProtocolValueError("invalid_finding_kind") from exc
    try:
        origin = FindingOrigin(source["origin"])
    except (TypeError, ValueError) as exc:
        raise ProtocolValueError("invalid_finding_origin") from exc
    refs_value = source["subject_refs"]
    if type(refs_value) is not tuple:
        raise ProtocolValueError("invalid_finding_subject_refs")
    provenance_value = _optional_field(source, "provenance")
    return Finding(
        finding_id=finding_id(source["finding_id"]),
        kind=kind,
        origin=origin,
        priority=cast(int, source["priority"]),
        summary=cast(str, source["summary"]),
        detail=cast(str, source["detail"]),
        subject_refs=cast(tuple[EventId | ObligationId | ClaimId, ...], refs_value),
        policy_id=cast(str, source["policy_id"]),
        policy_version=cast(str, source["policy_version"]),
        subject_frontier=frontier_from_json(source["subject_frontier"]),
        coverage=coverage_from_json(cast(CanonicalJsonValue, source["coverage"])),
        provenance=(
            None if provenance_value is None else semantic_provenance_from_json(provenance_value)
        ),
    )


def finding_to_json(finding: Finding) -> JsonObject:
    """Encode one finding into its exact closed schema object."""

    if type(finding) is not Finding:
        raise ProtocolValueError("finding_json_shape_invalid")
    result: dict[str, JsonValue] = {
        "finding_id": finding.finding_id,
        "kind": finding.kind.value,
        "origin": finding.origin.value,
        "priority": finding.priority,
        "summary": finding.summary,
        "detail": finding.detail,
        "subject_refs": finding.subject_refs,
        "policy_id": finding.policy_id,
        "policy_version": finding.policy_version,
        "subject_frontier": finding.subject_frontier.as_wire(),
        "coverage": JsonObject(coverage_to_json(finding.coverage)),
    }
    if finding.provenance is not None:
        result["provenance"] = semantic_provenance_to_json(finding.provenance)
    return JsonObject(result)


type RankKey = tuple[int, int, int, int, int, int, int, int, int, bytes]


def rank_key(finding: Finding) -> RankKey:
    """Return the registered ascending lexicographic ranking tuple."""

    if type(finding) is not Finding:
        raise ProtocolValueError("invalid_ranked_findings")
    priority, actionable = FINDING_KIND_TRAITS[finding.kind]
    coverage = finding.coverage
    real_check_present = int(
        CheckType.DETERMINISTIC in coverage.check_types
        or CheckType.SEMANTIC_MODEL_DERIVED in coverage.check_types
    )
    origin_ordinal = int(finding.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED)
    return (
        priority,
        -int(actionable),
        -ARTIFACT_OBSERVATION_ORDER[coverage.artifact_observation],
        -EVIDENCE_IMMUTABILITY_ORDER[coverage.evidence_immutability],
        -LEDGER_FRESHNESS_ORDER[coverage.ledger_freshness],
        -AUTHORSHIP_ASSURANCE_ORDER[coverage.authorship_assurance],
        -real_check_present,
        len(coverage.known_gaps),
        origin_ordinal,
        finding.finding_id.encode("ascii"),
    )
