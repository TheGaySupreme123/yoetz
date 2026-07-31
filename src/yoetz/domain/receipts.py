"""Immutable receipt documents, exact JSON codecs, and compact rendering."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Literal, cast

from yoetz.domain.findings import (
    Finding,
    FindingOrigin,
    ResponseDisposition,
    WaiverScope,
    finding_from_json,
    finding_to_json,
)
from yoetz.domain.values import (
    ClaimId,
    EventId,
    EvidenceId,
    FindingId,
    Frontier,
    ObligationId,
    ReceiptId,
    ResultId,
    SessionId,
    TaskId,
    Timestamp,
    claim_id,
    event_id,
    evidence_id,
    finding_id,
    freeze_json,
    frontier_from_json,
    obligation_id,
    receipt_id,
    result_id,
    session_id,
    task_id,
    timestamp_from_string,
    validate_sha256_digest,
)
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.coverage import (
    Coverage,
    coverage_from_json,
    coverage_to_json,
    weakest,
)
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.models import ReceiptRedactionProfile

__all__ = [
    "OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP",
    "PolicyVersionEntry",
    "ReceiptConclusion",
    "ReceiptDocument",
    "ReceiptGap",
    "ReceiptObligation",
    "ReceiptObligationStatus",
    "ReceiptRedaction",
    "ReceiptRedactionCategory",
    "ReceiptRedactionProfile",
    "ReceiptRedactionReason",
    "ReceiptResponse",
    "ReceiptSection",
    "ReceiptSectionKey",
    "ReceiptVersionSlice",
    "SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP",
    "SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP",
    "SEMANTIC_REVIEW_NOT_CONFIGURED_GAP",
    "SEMANTIC_REVIEW_NOT_REQUESTED_GAP",
    "SchemaVersionEntry",
    "receipt_document_from_json",
    "receipt_document_to_json",
    "receipt_weakest_coverage",
    "render_receipt_compact",
]

# Structural receipt/check coverage gap codes for optional semantic relevance review.
# Distinct from policy-block; not-configured and evaluator failure share honest not-run wording.
# semantic_review_not_requested marks every deterministic-only check (semantic never attempted).
SEMANTIC_REVIEW_NOT_CONFIGURED_GAP: Final = "semantic_review_not_configured"
SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP: Final = "semantic_relevance_review_not_run"
# The review ran, but the inference channel withheld categories the review profile
# selected, so it judged the work without material it was configured to receive.
SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP: Final = "semantic_review_context_withheld"
SEMANTIC_REVIEW_NOT_REQUESTED_GAP: Final = "semantic_review_not_requested"
OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP: Final = "optional_semantic_review_blocked_by_policy"
_SEMANTIC_REVIEW_NOT_RUN_GAPS: Final = frozenset(
    {
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
        SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
        SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
    }
)


class ReceiptConclusion(str, Enum):  # noqa: UP042 - exact wire enum base
    NO_UNRESOLVED_DETERMINISTIC_FINDINGS = "no_unresolved_deterministic_findings"
    UNRESOLVED_FINDINGS_REMAIN = "unresolved_findings_remain"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


class ReceiptObligationStatus(str, Enum):  # noqa: UP042 - exact wire enum base
    OPEN = "open"
    RESOLVED = "resolved"
    SUPERSEDED = "superseded"
    WAIVED = "waived"


class ReceiptRedactionCategory(str, Enum):  # noqa: UP042 - exact wire enum base
    CLAIM_TEXT = "claim_text"
    EVIDENCE_CONTENT = "evidence_content"
    FINDING_DETAIL = "finding_detail"
    OBLIGATION_TEXT = "obligation_text"
    REPOSITORY_CONTENT = "repository_content"
    TRANSCRIPT_CONTENT = "transcript_content"


class ReceiptRedactionReason(str, Enum):  # noqa: UP042 - exact wire enum base
    INCLUDE_PROFILE_OMITTED = "include_profile_omitted"
    NEVER_SEND_REDACTED = "never_send_redacted"
    POLICY_REDACTED = "policy_redacted"
    SOURCE_REDACTED = "source_redacted"


class ReceiptSectionKey(str, Enum):  # noqa: UP042 - exact wire enum base
    SUMMARY = "summary"
    OUTSTANDING_WORK = "outstanding_work"
    FINDINGS_AND_DISPOSITIONS = "findings_and_dispositions"
    EVIDENCE_AND_CLAIM_BASIS = "evidence_and_claim_basis"
    LIMITATIONS_AND_COVERAGE = "limitations_and_coverage"
    VERSION_AND_POLICY_IDENTITY = "version_and_policy_identity"


_SUMMARY_SECTION_KEYS: Final = (
    ReceiptSectionKey.SUMMARY,
    ReceiptSectionKey.LIMITATIONS_AND_COVERAGE,
    ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY,
)
_STANDARD_SECTION_KEYS: Final = (
    ReceiptSectionKey.SUMMARY,
    ReceiptSectionKey.OUTSTANDING_WORK,
    ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS,
    ReceiptSectionKey.LIMITATIONS_AND_COVERAGE,
    ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY,
)
_FULL_SECTION_KEYS: Final = (
    ReceiptSectionKey.SUMMARY,
    ReceiptSectionKey.OUTSTANDING_WORK,
    ReceiptSectionKey.FINDINGS_AND_DISPOSITIONS,
    ReceiptSectionKey.EVIDENCE_AND_CLAIM_BASIS,
    ReceiptSectionKey.LIMITATIONS_AND_COVERAGE,
    ReceiptSectionKey.VERSION_AND_POLICY_IDENTITY,
)
_VALID_SECTION_KEY_SEQUENCES: Final = frozenset(
    {_SUMMARY_SECTION_KEYS, _STANDARD_SECTION_KEYS, _FULL_SECTION_KEYS}
)

_POLICY_ID_RE: Final = re.compile(
    r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$",
    re.ASCII,
)
_SCHEMA_ID_RE: Final = re.compile(
    r"^[a-z][a-z0-9]*(?:[-_/][a-z0-9.]+)*$",
    re.ASCII,
)
_VERSION_ID_RE: Final = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._/+:-]*$", re.ASCII)
_POSITIVE_DECIMAL_RE: Final = re.compile(r"^[1-9][0-9]*$", re.ASCII)
_UNSIGNED_DECIMAL_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)$", re.ASCII)
_GAP_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", re.ASCII)
_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991


def _is_actual_mapping(value: object) -> bool:
    try:
        return issubclass(type(value), Mapping)
    except BaseException:
        return False


def _closed_object(
    value: object,
    required: frozenset[str],
    optional: frozenset[str],
    reason: str,
) -> Mapping[object, object]:
    if not _is_actual_mapping(value):
        raise ProtocolValueError(reason)
    source = cast(Mapping[object, object], value)
    try:
        keys = tuple(source)
    except Exception as exc:
        raise ProtocolValueError(reason) from exc
    if any(type(key) is not str for key in keys):
        raise ProtocolValueError(reason)
    string_keys = cast(tuple[str, ...], keys)
    key_set = frozenset(string_keys)
    if len(string_keys) != len(key_set) or not required <= key_set or key_set - required - optional:
        raise ProtocolValueError(reason)
    return source


def _field(source: Mapping[object, object], key: str, reason: str) -> object:
    try:
        return source[key]
    except Exception as exc:
        raise ProtocolValueError(reason) from exc


def _array(value: object, reason: str) -> tuple[object, ...]:
    if type(value) is list:
        return tuple(cast(list[object], value))
    if type(value) is tuple:
        return cast(tuple[object, ...], value)
    raise ProtocolValueError(reason)


def _enum_value[T: Enum](value: object, enum_type: type[T], reason: str) -> T:
    if type(value) is not str:
        raise ProtocolValueError(reason)
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ProtocolValueError(reason) from exc


def _bounded_text(value: object, minimum: int, maximum: int, reason: str) -> str:
    if type(value) is not str:
        raise ProtocolValueError(reason)
    text = value
    length = len(text)
    if length < minimum or length > maximum:
        raise ProtocolValueError(reason)
    freeze_json(text)
    return text


def _validate_tuple(value: object, minimum: int, maximum: int, reason: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise ProtocolValueError(reason)
    values = cast(tuple[object, ...], value)
    if not minimum <= len(values) <= maximum:
        raise ProtocolValueError(reason)
    return values


def _validate_sorted_unique_strings(values: tuple[str, ...]) -> None:
    previous: bytes | None = None
    for value in values:
        current = value.encode("ascii")
        if previous is not None:
            if current == previous:
                raise ProtocolValueError("duplicate_set_member")
            if current < previous:
                raise ProtocolValueError("unsorted_set_field")
        previous = current


def _version_identity(value: object, reason: str) -> str:
    text = _bounded_text(value, 1, 256, reason)
    if _VERSION_ID_RE.fullmatch(text) is None:
        raise ProtocolValueError(reason)
    return text


def _schema_counter_version(value: object, reason: str) -> str:
    text = _bounded_text(value, 1, 19, reason)
    if _POSITIVE_DECIMAL_RE.fullmatch(text) is None:
        raise ProtocolValueError(reason)
    return text


def _subject_ref(value: object, reason: str) -> EventId | ObligationId | ClaimId:
    if type(value) is not str:
        raise ProtocolValueError(reason)
    text = value
    if text.startswith("evt_"):
        return event_id(text)
    if text.startswith("obl_"):
        return obligation_id(text)
    if text.startswith("clm_"):
        return claim_id(text)
    raise ProtocolValueError(reason)


def _response_evidence_ref(value: object) -> EvidenceId | ResultId:
    if type(value) is not str:
        raise ProtocolValueError("invalid_receipt_response")
    text = value
    if text.startswith("evd_"):
        return evidence_id(text)
    if text.startswith("res_"):
        return result_id(text)
    raise ProtocolValueError("invalid_receipt_response")


@dataclass(frozen=True, slots=True)
class PolicyVersionEntry:
    policy_id: str
    policy_version: str

    def __post_init__(self) -> None:
        policy_id_value = _bounded_text(
            self.policy_id,
            1,
            128,
            "invalid_receipt_version_slice",
        )
        if _POLICY_ID_RE.fullmatch(policy_id_value) is None:
            raise ProtocolValueError("invalid_receipt_version_slice")
        policy_version_value = _version_identity(
            self.policy_version,
            "invalid_receipt_version_slice",
        )
        object.__setattr__(self, "policy_id", policy_id_value)
        object.__setattr__(self, "policy_version", policy_version_value)


@dataclass(frozen=True, slots=True)
class SchemaVersionEntry:
    schema_id: str
    schema_version: str

    def __post_init__(self) -> None:
        schema_id_value = _bounded_text(
            self.schema_id,
            1,
            256,
            "invalid_receipt_version_slice",
        )
        if _SCHEMA_ID_RE.fullmatch(schema_id_value) is None:
            raise ProtocolValueError("invalid_receipt_version_slice")
        schema_version_value = _version_identity(
            self.schema_version,
            "invalid_receipt_version_slice",
        )
        object.__setattr__(self, "schema_id", schema_id_value)
        object.__setattr__(self, "schema_version", schema_version_value)


@dataclass(frozen=True, slots=True)
class ReceiptVersionSlice:
    package_name: Literal["yoetz"]
    package_version: str
    protocol_version: str
    engine_version: str
    projection_version: str
    object_format_version: str
    catalog_schema_version: str
    bundle_schema_version: str
    policy_versions: tuple[PolicyVersionEntry, ...]
    schema_versions: tuple[SchemaVersionEntry, ...]
    resource_manifest_digest: str

    def __post_init__(self) -> None:
        reason = "invalid_receipt_version_slice"
        if type(self.package_name) is not str or self.package_name != "yoetz":
            raise ProtocolValueError(reason)
        for name in (
            "package_version",
            "protocol_version",
            "engine_version",
            "projection_version",
            "object_format_version",
        ):
            object.__setattr__(self, name, _version_identity(getattr(self, name), reason))
        object.__setattr__(
            self,
            "catalog_schema_version",
            _schema_counter_version(self.catalog_schema_version, reason),
        )
        object.__setattr__(
            self,
            "bundle_schema_version",
            _schema_counter_version(self.bundle_schema_version, reason),
        )
        policies = _validate_tuple(self.policy_versions, 1, 16, reason)
        if any(type(entry) is not PolicyVersionEntry for entry in policies):
            raise ProtocolValueError(reason)
        policy_entries = cast(tuple[PolicyVersionEntry, ...], policies)
        _validate_sorted_unique_strings(
            tuple(f"{entry.policy_id}\x00{entry.policy_version}" for entry in policy_entries)
        )
        schemas = _validate_tuple(self.schema_versions, 1, 64, reason)
        if any(type(entry) is not SchemaVersionEntry for entry in schemas):
            raise ProtocolValueError(reason)
        schema_entries = cast(tuple[SchemaVersionEntry, ...], schemas)
        _validate_sorted_unique_strings(
            tuple(f"{entry.schema_id}\x00{entry.schema_version}" for entry in schema_entries)
        )
        object.__setattr__(
            self,
            "resource_manifest_digest",
            validate_sha256_digest(self.resource_manifest_digest),
        )


@dataclass(frozen=True, slots=True)
class ReceiptObligation:
    obligation_id: ObligationId
    status: ReceiptObligationStatus
    source_refs: tuple[EventId | ObligationId | ClaimId, ...]
    summary: str | None = None

    def __post_init__(self) -> None:
        reason = "invalid_receipt_obligation"
        object.__setattr__(self, "obligation_id", obligation_id(self.obligation_id))
        if type(self.status) is not ReceiptObligationStatus:
            raise ProtocolValueError(reason)
        raw_refs = _validate_tuple(self.source_refs, 0, 64, reason)
        refs = tuple(_subject_ref(value, reason) for value in raw_refs)
        _validate_sorted_unique_strings(cast(tuple[str, ...], refs))
        object.__setattr__(self, "source_refs", refs)
        if self.summary is not None:
            object.__setattr__(self, "summary", _bounded_text(self.summary, 1, 8192, reason))


@dataclass(frozen=True, slots=True)
class ReceiptResponse:
    finding_id: FindingId
    finding_frontier: Frontier
    disposition: ResponseDisposition
    evidence_refs: tuple[EvidenceId | ResultId, ...]
    reason: str | None = None
    waiver_scope: WaiverScope | None = None
    waiver_expiry: Timestamp | None = None

    def __post_init__(self) -> None:
        invalid = "invalid_receipt_response"
        object.__setattr__(self, "finding_id", finding_id(self.finding_id))
        if type(self.finding_frontier) is not Frontier:
            raise ProtocolValueError(invalid)
        if type(self.disposition) is not ResponseDisposition:
            raise ProtocolValueError(invalid)
        raw_refs = _validate_tuple(self.evidence_refs, 0, 64, invalid)
        refs = tuple(_response_evidence_ref(value) for value in raw_refs)
        _validate_sorted_unique_strings(cast(tuple[str, ...], refs))
        object.__setattr__(self, "evidence_refs", refs)
        if self.reason is not None:
            object.__setattr__(self, "reason", _bounded_text(self.reason, 1, 8192, invalid))
        if self.waiver_scope is not None and type(self.waiver_scope) is not WaiverScope:
            raise ProtocolValueError(invalid)
        if self.waiver_expiry is not None and type(self.waiver_expiry) is not Timestamp:
            raise ProtocolValueError(invalid)
        if self.disposition is ResponseDisposition.ACKNOWLEDGED:
            if self.waiver_scope is not None or self.waiver_expiry is not None:
                raise ProtocolValueError(invalid)
        elif self.disposition is ResponseDisposition.REJECTED:
            if (
                self.reason is None
                or self.waiver_scope is not None
                or self.waiver_expiry is not None
            ):
                raise ProtocolValueError(invalid)
        elif self.disposition is ResponseDisposition.WAIVED:
            if self.reason is None or self.waiver_scope is None:
                raise ProtocolValueError(invalid)


@dataclass(frozen=True, slots=True)
class ReceiptGap:
    code: str
    subject_refs: tuple[EventId | ObligationId | ClaimId, ...]
    detail: str | None = None

    def __post_init__(self) -> None:
        invalid = "invalid_receipt_gap"
        code_value = _bounded_text(self.code, 1, 128, invalid)
        if _GAP_CODE_RE.fullmatch(code_value) is None:
            raise ProtocolValueError(invalid)
        object.__setattr__(self, "code", code_value)
        raw_refs = _validate_tuple(self.subject_refs, 0, 16, invalid)
        refs = tuple(_subject_ref(value, invalid) for value in raw_refs)
        _validate_sorted_unique_strings(cast(tuple[str, ...], refs))
        object.__setattr__(self, "subject_refs", refs)
        if self.detail is not None:
            object.__setattr__(self, "detail", _bounded_text(self.detail, 1, 4096, invalid))


@dataclass(frozen=True, slots=True)
class ReceiptRedaction:
    category: ReceiptRedactionCategory
    reason: ReceiptRedactionReason
    count: int

    def __post_init__(self) -> None:
        invalid = "invalid_receipt_redaction"
        if type(self.category) is not ReceiptRedactionCategory:
            raise ProtocolValueError(invalid)
        if type(self.reason) is not ReceiptRedactionReason:
            raise ProtocolValueError(invalid)
        if type(self.count) is not int or not 0 <= self.count <= 9_999_999_999_999_999:
            raise ProtocolValueError(invalid)


@dataclass(frozen=True, slots=True)
class ReceiptSection:
    key: ReceiptSectionKey
    title: str
    body: str
    items: tuple[str, ...]
    coverage_note: str | None = None

    def __post_init__(self) -> None:
        invalid = "invalid_receipt_section"
        if type(self.key) is not ReceiptSectionKey:
            raise ProtocolValueError(invalid)
        object.__setattr__(self, "title", _bounded_text(self.title, 1, 128, invalid))
        object.__setattr__(self, "body", _bounded_text(self.body, 1, 32768, invalid))
        raw_items = _validate_tuple(self.items, 0, 64, invalid)
        items = tuple(_bounded_text(item, 1, 8192, invalid) for item in raw_items)
        object.__setattr__(self, "items", items)
        if self.coverage_note is not None:
            object.__setattr__(
                self,
                "coverage_note",
                _bounded_text(self.coverage_note, 1, 4096, invalid),
            )


@dataclass(frozen=True, slots=True)
class ReceiptDocument:
    schema_version: Literal["1.0.0"] = field(default="1.0.0", init=False)
    receipt_id: ReceiptId
    task_id: TaskId
    session_id: SessionId
    generated_at: Timestamp
    subject_frontier: Frontier
    conclusion: ReceiptConclusion
    suppressed_finding_count: int
    versions: ReceiptVersionSlice
    coverage: Coverage
    findings: tuple[Finding, ...]
    obligations: tuple[ReceiptObligation, ...]
    responses: tuple[ReceiptResponse, ...]
    claim_refs: tuple[ClaimId, ...]
    evidence_refs: tuple[EvidenceId, ...]
    gaps: tuple[ReceiptGap, ...]
    redactions: tuple[ReceiptRedaction, ...]
    sections: tuple[ReceiptSection, ...]

    def __post_init__(self) -> None:
        invalid = "invalid_receipt_document"
        object.__setattr__(self, "receipt_id", receipt_id(self.receipt_id))
        object.__setattr__(self, "task_id", task_id(self.task_id))
        object.__setattr__(self, "session_id", session_id(self.session_id))
        if type(self.generated_at) is not Timestamp or type(self.subject_frontier) is not Frontier:
            raise ProtocolValueError(invalid)
        if type(self.conclusion) is not ReceiptConclusion:
            raise ProtocolValueError("invalid_receipt_conclusion")
        if (
            type(self.suppressed_finding_count) is not int
            or not 0 <= self.suppressed_finding_count <= _MAX_SAFE_INTEGER
        ):
            raise ProtocolValueError(invalid)
        if type(self.versions) is not ReceiptVersionSlice or type(self.coverage) is not Coverage:
            raise ProtocolValueError(invalid)
        findings = _validate_tuple(self.findings, 0, 100, invalid)
        if any(type(value) is not Finding for value in findings):
            raise ProtocolValueError(invalid)
        obligations = _validate_tuple(self.obligations, 0, 100, invalid)
        if any(type(value) is not ReceiptObligation for value in obligations):
            raise ProtocolValueError(invalid)
        responses = _validate_tuple(self.responses, 0, 100, invalid)
        if any(type(value) is not ReceiptResponse for value in responses):
            raise ProtocolValueError(invalid)
        claim_values = _validate_tuple(self.claim_refs, 0, 100, invalid)
        claims = tuple(claim_id(value) for value in claim_values)
        _validate_sorted_unique_strings(cast(tuple[str, ...], claims))
        object.__setattr__(self, "claim_refs", claims)
        evidence_values = _validate_tuple(self.evidence_refs, 0, 100, invalid)
        evidence = tuple(evidence_id(value) for value in evidence_values)
        _validate_sorted_unique_strings(cast(tuple[str, ...], evidence))
        object.__setattr__(self, "evidence_refs", evidence)
        gaps = _validate_tuple(self.gaps, 0, 64, invalid)
        if any(type(value) is not ReceiptGap for value in gaps):
            raise ProtocolValueError(invalid)
        redactions = _validate_tuple(self.redactions, 0, 64, invalid)
        if any(type(value) is not ReceiptRedaction for value in redactions):
            raise ProtocolValueError(invalid)
        sections = _validate_tuple(self.sections, 3, 6, invalid)
        if any(type(value) is not ReceiptSection for value in sections):
            raise ProtocolValueError(invalid)
        section_keys = tuple(cast(ReceiptSection, section).key for section in sections)
        if section_keys not in _VALID_SECTION_KEY_SEQUENCES:
            raise ProtocolValueError("invalid_receipt_section_order")
        if (
            self.conclusion is ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS
            and self.suppressed_finding_count != 0
        ):
            raise ProtocolValueError(invalid)
        material_coverage = self.coverage
        for finding in cast(tuple[Finding, ...], findings):
            material_coverage = weakest(material_coverage, finding.coverage)
        if material_coverage != self.coverage:
            raise ProtocolValueError("receipt_coverage_mismatch")
        for gap in cast(tuple[ReceiptGap, ...], gaps):
            if gap.code not in self.coverage.known_gaps:
                raise ProtocolValueError("receipt_gap_not_in_coverage")


def _policy_version_from_json(value: object) -> PolicyVersionEntry:
    invalid = "invalid_receipt_version_slice"
    source = _closed_object(
        value,
        frozenset({"policy_id", "policy_version"}),
        frozenset(),
        invalid,
    )
    return PolicyVersionEntry(
        policy_id=cast(str, _field(source, "policy_id", invalid)),
        policy_version=cast(str, _field(source, "policy_version", invalid)),
    )


def _schema_version_from_json(value: object) -> SchemaVersionEntry:
    invalid = "invalid_receipt_version_slice"
    source = _closed_object(
        value,
        frozenset({"schema_id", "schema_version"}),
        frozenset(),
        invalid,
    )
    return SchemaVersionEntry(
        schema_id=cast(str, _field(source, "schema_id", invalid)),
        schema_version=cast(str, _field(source, "schema_version", invalid)),
    )


def _version_slice_from_json(value: object) -> ReceiptVersionSlice:
    invalid = "invalid_receipt_version_slice"
    keys = frozenset(
        {
            "package_name",
            "package_version",
            "protocol_version",
            "engine_version",
            "projection_version",
            "object_format_version",
            "catalog_schema_version",
            "bundle_schema_version",
            "policy_versions",
            "schema_versions",
            "resource_manifest_digest",
        }
    )
    source = _closed_object(value, keys, frozenset(), invalid)
    policies = tuple(
        _policy_version_from_json(item)
        for item in _array(_field(source, "policy_versions", invalid), invalid)
    )
    schemas = tuple(
        _schema_version_from_json(item)
        for item in _array(_field(source, "schema_versions", invalid), invalid)
    )
    return ReceiptVersionSlice(
        package_name=cast(Literal["yoetz"], _field(source, "package_name", invalid)),
        package_version=cast(str, _field(source, "package_version", invalid)),
        protocol_version=cast(str, _field(source, "protocol_version", invalid)),
        engine_version=cast(str, _field(source, "engine_version", invalid)),
        projection_version=cast(str, _field(source, "projection_version", invalid)),
        object_format_version=cast(str, _field(source, "object_format_version", invalid)),
        catalog_schema_version=cast(str, _field(source, "catalog_schema_version", invalid)),
        bundle_schema_version=cast(str, _field(source, "bundle_schema_version", invalid)),
        policy_versions=policies,
        schema_versions=schemas,
        resource_manifest_digest=cast(str, _field(source, "resource_manifest_digest", invalid)),
    )


def _obligation_from_json(value: object) -> ReceiptObligation:
    invalid = "invalid_receipt_obligation"
    source = _closed_object(
        value,
        frozenset({"obligation_id", "status", "source_refs"}),
        frozenset({"summary"}),
        invalid,
    )
    refs = tuple(
        _subject_ref(item, invalid)
        for item in _array(_field(source, "source_refs", invalid), invalid)
    )
    keys = frozenset(cast(tuple[str, ...], tuple(source)))
    return ReceiptObligation(
        obligation_id=obligation_id(_field(source, "obligation_id", invalid)),
        status=_enum_value(
            _field(source, "status", invalid),
            ReceiptObligationStatus,
            invalid,
        ),
        source_refs=refs,
        summary=(cast(str, _field(source, "summary", invalid)) if "summary" in keys else None),
    )


def _response_from_json(value: object) -> ReceiptResponse:
    invalid = "invalid_receipt_response"
    source = _closed_object(
        value,
        frozenset({"finding_id", "finding_frontier", "disposition", "evidence_refs"}),
        frozenset({"reason", "waiver_scope", "waiver_expiry"}),
        invalid,
    )
    keys = frozenset(cast(tuple[str, ...], tuple(source)))
    refs = tuple(
        _response_evidence_ref(item)
        for item in _array(_field(source, "evidence_refs", invalid), invalid)
    )
    return ReceiptResponse(
        finding_id=finding_id(_field(source, "finding_id", invalid)),
        finding_frontier=frontier_from_json(_field(source, "finding_frontier", invalid)),
        disposition=_enum_value(
            _field(source, "disposition", invalid),
            ResponseDisposition,
            invalid,
        ),
        evidence_refs=refs,
        reason=cast(str, _field(source, "reason", invalid)) if "reason" in keys else None,
        waiver_scope=(
            _enum_value(_field(source, "waiver_scope", invalid), WaiverScope, invalid)
            if "waiver_scope" in keys
            else None
        ),
        waiver_expiry=(
            timestamp_from_string(_field(source, "waiver_expiry", invalid))
            if "waiver_expiry" in keys
            else None
        ),
    )


def _gap_from_json(value: object) -> ReceiptGap:
    invalid = "invalid_receipt_gap"
    source = _closed_object(
        value,
        frozenset({"code", "subject_refs"}),
        frozenset({"detail"}),
        invalid,
    )
    keys = frozenset(cast(tuple[str, ...], tuple(source)))
    refs = tuple(
        _subject_ref(item, invalid)
        for item in _array(_field(source, "subject_refs", invalid), invalid)
    )
    return ReceiptGap(
        code=cast(str, _field(source, "code", invalid)),
        subject_refs=refs,
        detail=cast(str, _field(source, "detail", invalid)) if "detail" in keys else None,
    )


def _redaction_from_json(value: object) -> ReceiptRedaction:
    invalid = "invalid_receipt_redaction"
    source = _closed_object(
        value,
        frozenset({"category", "reason", "count"}),
        frozenset(),
        invalid,
    )
    raw_count = _field(source, "count", invalid)
    if (
        type(raw_count) is not str
        or len(raw_count) > 16
        or _UNSIGNED_DECIMAL_RE.fullmatch(raw_count) is None
    ):
        raise ProtocolValueError(invalid)
    return ReceiptRedaction(
        category=_enum_value(
            _field(source, "category", invalid),
            ReceiptRedactionCategory,
            invalid,
        ),
        reason=_enum_value(
            _field(source, "reason", invalid),
            ReceiptRedactionReason,
            invalid,
        ),
        count=int(raw_count),
    )


def _section_from_json(value: object) -> ReceiptSection:
    invalid = "invalid_receipt_section"
    source = _closed_object(
        value,
        frozenset({"key", "title", "body", "items"}),
        frozenset({"coverage_note"}),
        invalid,
    )
    keys = frozenset(cast(tuple[str, ...], tuple(source)))
    items = tuple(cast(str, item) for item in _array(_field(source, "items", invalid), invalid))
    return ReceiptSection(
        key=_enum_value(_field(source, "key", invalid), ReceiptSectionKey, invalid),
        title=cast(str, _field(source, "title", invalid)),
        body=cast(str, _field(source, "body", invalid)),
        items=items,
        coverage_note=(
            cast(str, _field(source, "coverage_note", invalid)) if "coverage_note" in keys else None
        ),
    )


def receipt_document_from_json(value: object) -> ReceiptDocument:
    """Decode the exact closed receipt-document schema into immutable domain values."""

    invalid = "receipt_json_shape_invalid"
    keys = frozenset(
        {
            "schema_version",
            "receipt_id",
            "task_id",
            "session_id",
            "generated_at",
            "subject_frontier",
            "conclusion",
            "suppressed_finding_count",
            "versions",
            "coverage",
            "findings",
            "obligations",
            "responses",
            "claim_refs",
            "evidence_refs",
            "gaps",
            "redactions",
            "sections",
        }
    )
    source = _closed_object(value, keys, frozenset(), invalid)
    if _field(source, "schema_version", invalid) != "1.0.0":
        raise ProtocolValueError(invalid)
    raw_suppressed = _field(source, "suppressed_finding_count", invalid)
    if type(raw_suppressed) is not int:
        raise ProtocolValueError("invalid_receipt_document")
    findings = tuple(
        finding_from_json(freeze_json(item))
        for item in _array(_field(source, "findings", invalid), invalid)
    )
    obligations = tuple(
        _obligation_from_json(item)
        for item in _array(_field(source, "obligations", invalid), invalid)
    )
    responses = tuple(
        _response_from_json(item) for item in _array(_field(source, "responses", invalid), invalid)
    )
    claims = tuple(
        claim_id(item) for item in _array(_field(source, "claim_refs", invalid), invalid)
    )
    evidence = tuple(
        evidence_id(item) for item in _array(_field(source, "evidence_refs", invalid), invalid)
    )
    gaps = tuple(_gap_from_json(item) for item in _array(_field(source, "gaps", invalid), invalid))
    redactions = tuple(
        _redaction_from_json(item)
        for item in _array(_field(source, "redactions", invalid), invalid)
    )
    sections = tuple(
        _section_from_json(item) for item in _array(_field(source, "sections", invalid), invalid)
    )
    return ReceiptDocument(
        receipt_id=receipt_id(_field(source, "receipt_id", invalid)),
        task_id=task_id(_field(source, "task_id", invalid)),
        session_id=session_id(_field(source, "session_id", invalid)),
        generated_at=timestamp_from_string(_field(source, "generated_at", invalid)),
        subject_frontier=frontier_from_json(_field(source, "subject_frontier", invalid)),
        conclusion=_enum_value(
            _field(source, "conclusion", invalid),
            ReceiptConclusion,
            "invalid_receipt_conclusion",
        ),
        suppressed_finding_count=raw_suppressed,
        versions=_version_slice_from_json(_field(source, "versions", invalid)),
        coverage=coverage_from_json(cast(CanonicalJsonValue, _field(source, "coverage", invalid))),
        findings=findings,
        obligations=obligations,
        responses=responses,
        claim_refs=claims,
        evidence_refs=evidence,
        gaps=gaps,
        redactions=redactions,
        sections=sections,
    )


def _frontier_to_json(frontier: Frontier) -> dict[str, object]:
    return {key: value for key, value in frontier.as_wire().items()}


def _version_slice_to_json(value: ReceiptVersionSlice) -> dict[str, object]:
    return {
        "package_name": value.package_name,
        "package_version": value.package_version,
        "protocol_version": value.protocol_version,
        "engine_version": value.engine_version,
        "projection_version": value.projection_version,
        "object_format_version": value.object_format_version,
        "catalog_schema_version": value.catalog_schema_version,
        "bundle_schema_version": value.bundle_schema_version,
        "policy_versions": [
            {"policy_id": entry.policy_id, "policy_version": entry.policy_version}
            for entry in value.policy_versions
        ],
        "schema_versions": [
            {"schema_id": entry.schema_id, "schema_version": entry.schema_version}
            for entry in value.schema_versions
        ],
        "resource_manifest_digest": value.resource_manifest_digest,
    }


def _obligation_to_json(value: ReceiptObligation) -> dict[str, object]:
    result: dict[str, object] = {
        "obligation_id": value.obligation_id,
        "status": value.status.value,
        "source_refs": list(value.source_refs),
    }
    if value.summary is not None:
        result["summary"] = value.summary
    return result


def _response_to_json(value: ReceiptResponse) -> dict[str, object]:
    result: dict[str, object] = {
        "finding_id": value.finding_id,
        "finding_frontier": _frontier_to_json(value.finding_frontier),
        "disposition": value.disposition.value,
        "evidence_refs": list(value.evidence_refs),
    }
    if value.reason is not None:
        result["reason"] = value.reason
    if value.waiver_scope is not None:
        result["waiver_scope"] = value.waiver_scope.value
    if value.waiver_expiry is not None:
        result["waiver_expiry"] = value.waiver_expiry.wire
    return result


def _gap_to_json(value: ReceiptGap) -> dict[str, object]:
    result: dict[str, object] = {"code": value.code, "subject_refs": list(value.subject_refs)}
    if value.detail is not None:
        result["detail"] = value.detail
    return result


def _redaction_to_json(value: ReceiptRedaction) -> dict[str, object]:
    return {
        "category": value.category.value,
        "reason": value.reason.value,
        "count": str(value.count),
    }


def _section_to_json(value: ReceiptSection) -> dict[str, object]:
    result: dict[str, object] = {
        "key": value.key.value,
        "title": value.title,
        "body": value.body,
        "items": list(value.items),
    }
    if value.coverage_note is not None:
        result["coverage_note"] = value.coverage_note
    return result


def receipt_document_to_json(document: ReceiptDocument) -> dict[str, object]:
    """Encode a receipt document as the exact closed schema object."""

    if type(document) is not ReceiptDocument:
        raise ProtocolValueError("invalid_receipt_document")
    return {
        "schema_version": document.schema_version,
        "receipt_id": document.receipt_id,
        "task_id": document.task_id,
        "session_id": document.session_id,
        "generated_at": document.generated_at.wire,
        "subject_frontier": _frontier_to_json(document.subject_frontier),
        "conclusion": document.conclusion.value,
        "suppressed_finding_count": document.suppressed_finding_count,
        "versions": _version_slice_to_json(document.versions),
        "coverage": coverage_to_json(document.coverage),
        "findings": [finding_to_json(finding) for finding in document.findings],
        "obligations": [_obligation_to_json(value) for value in document.obligations],
        "responses": [_response_to_json(value) for value in document.responses],
        "claim_refs": list(document.claim_refs),
        "evidence_refs": list(document.evidence_refs),
        "gaps": [_gap_to_json(value) for value in document.gaps],
        "redactions": [_redaction_to_json(value) for value in document.redactions],
        "sections": [_section_to_json(value) for value in document.sections],
    }


def receipt_weakest_coverage(document: ReceiptDocument) -> Coverage:
    """Fold the document coverage with carried findings in stored order."""

    if type(document) is not ReceiptDocument:
        raise ProtocolValueError("invalid_receipt_document")
    result = document.coverage
    for finding in document.findings:
        result = weakest(result, finding.coverage)
    return result


def _waiver_for_render(document: ReceiptDocument) -> ReceiptResponse | None:
    for response in document.responses:
        if response.disposition is ResponseDisposition.WAIVED:
            return response
    return None


def render_receipt_compact(document: ReceiptDocument) -> str:
    """Render the bounded, newline-free compact receipt sentence frozen by v0.1 fixtures."""

    if type(document) is not ReceiptDocument:
        raise ProtocolValueError("invalid_receipt_document")
    frontier = document.subject_frontier.sequence
    prefix = f"Yoetz receipt at frontier {frontier}: "
    gap_codes = frozenset(gap.code for gap in document.gaps)

    if "encryption_key_unavailable" in gap_codes:
        return (
            prefix
            + "coverage is insufficient because a referenced encrypted object cannot be opened "
            "with the available keys. No payload content is shown."
        )
    if "content_unavailable_redacted" in gap_codes:
        return (
            prefix + "coverage is insufficient because a referenced object was redacted. "
            "No payload content is shown."
        )
    if OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP in gap_codes:
        return (
            prefix + "coverage is insufficient because optional semantic review was blocked before "
            "dispatch by network-egress policy. No provider attempt or semantic finding was "
            "recorded."
        )
    if gap_codes & _SEMANTIC_REVIEW_NOT_RUN_GAPS:
        if document.conclusion is ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN:
            count = len(document.findings)
            noun = "finding" if count == 1 else "findings"
            verb = "remains" if count == 1 else "remain"
            return (
                prefix + f"{count} unresolved {noun} {verb}; semantic relevance review was not run."
            )
        if document.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE:
            return prefix + "coverage is insufficient; semantic relevance review was not run."
        return (
            prefix + "no unresolved deterministic issue was found in the published record; "
            "semantic relevance review was not run."
        )
    if {
        "import_source_range_not_universal",
        "unobserved_work_outside_accepted_ranges",
    } <= gap_codes:
        return (
            prefix
            + "coverage is insufficient. A bounded Codex import processed its accepted source "
            "range, but declared gaps prevent a claim about all work."
        )

    waiver = _waiver_for_render(document)
    if waiver is not None and waiver.waiver_expiry is not None:
        same_frontier = waiver.finding_frontier == document.subject_frontier
        if (
            document.conclusion is ReceiptConclusion.NO_UNRESOLVED_DETERMINISTIC_FINDINGS
            and same_frontier
            and waiver.waiver_expiry >= document.generated_at
        ):
            return (
                prefix
                + "no unresolved deterministic findings are presented because the one finding "
                f"has an active local-human finding-only waiver through {waiver.waiver_expiry.wire}. "
                "The waiver does not apply to another frontier."
            )
        if document.conclusion is ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN:
            if not same_frontier:
                return (
                    prefix + "one unresolved finding remains. A waiver recorded for frontier "
                    f"{waiver.finding_frontier.sequence} has no effect on this frontier."
                )
            if waiver.waiver_expiry < document.generated_at:
                return (
                    prefix
                    + "one unresolved finding remains. Its recorded finding-only waiver expired "
                    f"at {waiver.waiver_expiry.wire} and is not active."
                )

    if any(finding.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED for finding in document.findings):
        return (
            prefix
            + "one advisory semantic finding remains unresolved. Semantic review completed, but "
            "it does not upgrade deterministic assurance or prove correctness."
        )
    if document.conclusion is ReceiptConclusion.UNRESOLVED_FINDINGS_REMAIN:
        if len(document.findings) == 3:
            return (
                prefix
                + "unresolved findings remain. Three current findings are shown: one acknowledged, "
                "one disputed by a rejection, and one without a response."
            )
        count = len(document.findings)
        noun = "finding" if count == 1 else "findings"
        verb = "remains" if count == 1 else "remain"
        return prefix + f"{count} unresolved {noun} {verb}."
    if document.conclusion is ReceiptConclusion.INSUFFICIENT_COVERAGE:
        return prefix + "coverage is insufficient. Declared gaps bound this receipt's conclusion."

    limitations = next(
        (
            section.body
            for section in document.sections
            if section.key is ReceiptSectionKey.LIMITATIONS_AND_COVERAGE
        ),
        "",
    )
    if "referenced immutable object was available" in limitations:
        return (
            prefix + "no unresolved deterministic findings were recorded. The referenced immutable "
            "object was available at build time."
        )
    return (
        prefix
        + "no unresolved deterministic findings were recorded. Coverage is current cooperative "
        "deterministic evidence; this is not proof of correctness."
    )
