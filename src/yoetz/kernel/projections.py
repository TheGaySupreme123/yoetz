"""Immutable generation-1 work projections and canonical snapshots."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Protocol, cast

from yoetz.domain.events import (
    PAYLOAD_TYPES,
    ActionRecordedPayload,
    AssignmentRecordedPayload,
    ClaimRecordedPayload,
    DecisionRecordedPayload,
    EventPayload,
    EventSchema,
    EvidenceRecordedPayload,
    ObligationChangeKind,
    ObligationPublishedPayload,
    PlanPublishedPayload,
    PlanRevisedPayload,
    ResponseRecordedPayload,
    ResultRecordedPayload,
    decode_payload,
    encode_payload,
)
from yoetz.domain.findings import CheckVerdict, Finding
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
    validate_sha256_digest,
)
from yoetz.domain.values import (
    JsonValue as DomainJsonValue,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_integer_string,
    parse_canonical_integer_string,
)
from yoetz.protocol.coverage import (
    Coverage,
    LedgerFreshness,
    coverage_from_json,
    coverage_to_json,
)

__all__ = [
    "PROJECTION_GENERATION",
    "PROJECTION_VERSION",
    "ContradictionKey",
    "ContradictionRecord",
    "DecisionProjectionRecord",
    "EvidenceProjectionRecord",
    "FindingProjectionRecord",
    "LatestTestedState",
    "ObligationProjectionRecord",
    "PlanProjectionRecord",
    "ProjectionRecord",
    "ProjectionState",
    "empty_projection_state",
    "projection_digest",
    "projection_from_snapshot",
    "projection_snapshot",
    "unanswered_finding_count",
]

PROJECTION_VERSION: Final = "yoetz/0.1.0"
PROJECTION_GENERATION: Final = 1

_MAX_SAFE_INTEGER: Final = 2**53 - 1
_MAX_SQLITE_SIGNED_INTEGER: Final = 2**63 - 1
_MAX_COVERAGE_GAPS: Final = 64
_SCHEMA_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z][a-z0-9_]{0,63}$",
    re.ASCII,
)
_SEMVER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$",
    re.ASCII,
)


def _invalid() -> ValueError:
    return ValueError("invalid_projection_state")


def _positive_frontier(value: object) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_SQLITE_SIGNED_INTEGER:
        raise _invalid()
    return value


def _nonnegative_safe_integer(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE_INTEGER:
        raise _invalid()
    return value


def _ascii_sorted_unique(values: tuple[str, ...]) -> bool:
    if type(values) is not tuple:
        return False
    try:
        return values == tuple(sorted(set(values), key=lambda item: item.encode("ascii")))
    except AttributeError, UnicodeEncodeError:
        return False


def _validate_claim_or_event(value: object) -> ClaimId | EventId:
    try:
        if type(value) is str and value.startswith("clm_"):
            return claim_id(value)
        return event_id(value)
    except ValueError as exc:
        raise _invalid() from exc


def _validate_missing_target(value: object) -> str:
    if type(value) is not str:
        raise _invalid()
    validators = {
        "act_": action_id,
        "clm_": claim_id,
        "evd_": evidence_id,
        "evt_": event_id,
        "fnd_": finding_id,
        "obl_": obligation_id,
        "res_": result_id,
    }
    for prefix, validator in validators.items():
        if value.startswith(prefix):
            try:
                validator(value)
            except ValueError as exc:
                raise _invalid() from exc
            return value
    raise _invalid()


def _validate_gap(marker: object) -> str:
    if type(marker) is not str:
        raise _invalid()
    if marker.startswith("unknown_event:"):
        parts = marker.split(":", 2)
        if len(parts) != 3 or "@" not in parts[2]:
            raise _invalid()
        schema_name, schema_version = parts[2].rsplit("@", 1)
        try:
            event_id(parts[1])
        except ValueError as exc:
            raise _invalid() from exc
        if (
            _SCHEMA_NAME_PATTERN.fullmatch(schema_name) is None
            or _SEMVER_PATTERN.fullmatch(schema_version) is None
        ):
            raise _invalid()
        return marker
    if marker.startswith("redacted_event:"):
        try:
            event_id(marker.removeprefix("redacted_event:"))
        except ValueError as exc:
            raise _invalid() from exc
        return marker
    if marker.startswith("redacted_object:"):
        try:
            object_id(marker.removeprefix("redacted_object:"))
        except ValueError as exc:
            raise _invalid() from exc
        return marker
    if marker.startswith("missing_ref:"):
        parts = marker.split(":", 2)
        if len(parts) != 3:
            raise _invalid()
        try:
            event_id(parts[1])
        except ValueError as exc:
            raise _invalid() from exc
        _validate_missing_target(parts[2])
        return marker
    raise _invalid()


@dataclass(frozen=True, slots=True)
class ProjectionRecord[T]:
    payload: T | None
    payload_digest: str
    redacted: bool
    source_event_id: EventId
    source_frontier: int

    def __post_init__(self) -> None:
        try:
            validate_sha256_digest(self.payload_digest)
            object.__setattr__(self, "source_event_id", event_id(self.source_event_id))
        except ValueError as exc:
            raise _invalid() from exc
        if type(self.redacted) is not bool or (self.payload is None) is not self.redacted:
            raise _invalid()
        object.__setattr__(self, "source_frontier", _positive_frontier(self.source_frontier))


class _ProjectionRecordLike(Protocol):
    @property
    def payload(self) -> object | None: ...

    @property
    def payload_digest(self) -> str: ...

    @property
    def redacted(self) -> bool: ...

    @property
    def source_event_id(self) -> EventId: ...

    @property
    def source_frontier(self) -> int: ...


@dataclass(frozen=True, slots=True)
class PlanProjectionRecord(ProjectionRecord[PlanPublishedPayload | PlanRevisedPayload]):
    superseded_by_plan_version: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.payload is not None and type(self.payload) not in {
            PlanPublishedPayload,
            PlanRevisedPayload,
        }:
            raise _invalid()
        if self.superseded_by_plan_version is not None:
            if (
                type(self.superseded_by_plan_version) is not int
                or not 1 <= self.superseded_by_plan_version <= _MAX_SAFE_INTEGER
            ):
                raise _invalid()


@dataclass(frozen=True, slots=True)
class ObligationProjectionRecord(ProjectionRecord[ObligationPublishedPayload]):
    plan_change: ObligationChangeKind | None = None
    plan_change_reason: str | None = None
    superseded_by_obligation_ids: tuple[ObligationId, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.payload is not None and type(self.payload) is not ObligationPublishedPayload:
            raise _invalid()
        if self.plan_change is not None and type(self.plan_change) is not ObligationChangeKind:
            raise _invalid()
        if self.plan_change_reason is not None:
            if (
                type(self.plan_change_reason) is not str
                or not self.plan_change_reason
                or len(self.plan_change_reason.encode("utf-8")) > 4_096
            ):
                raise _invalid()
        if type(self.superseded_by_obligation_ids) is not tuple:
            raise _invalid()
        try:
            replacements = tuple(
                obligation_id(value) for value in self.superseded_by_obligation_ids
            )
        except ValueError as exc:
            raise _invalid() from exc
        if not _ascii_sorted_unique(cast(tuple[str, ...], replacements)):
            raise _invalid()
        object.__setattr__(self, "superseded_by_obligation_ids", replacements)
        if self.plan_change is None and (self.plan_change_reason is not None or replacements):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class DecisionProjectionRecord(ProjectionRecord[DecisionRecordedPayload]):
    superseded_by_event_id: EventId | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.payload is not None and type(self.payload) is not DecisionRecordedPayload:
            raise _invalid()
        if self.superseded_by_event_id is not None:
            try:
                object.__setattr__(
                    self,
                    "superseded_by_event_id",
                    event_id(self.superseded_by_event_id),
                )
            except ValueError as exc:
                raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class FindingProjectionRecord(ProjectionRecord[Finding]):
    """One recorded finding plus its proof-based resolution fact.

    ``resolved_by_check_event_id`` names the later ``check_recorded`` event whose recorded state
    qualified to resolve this finding's issue (``kernel/finding_resolution.py``). It is ``None``
    while the finding is current. A response disposition never sets it; a check that returns the
    finding again clears it; redacting the proving check clears it.
    """

    resolved_by_check_event_id: EventId | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.payload is not None and type(self.payload) is not Finding:
            raise _invalid()
        if self.resolved_by_check_event_id is not None:
            try:
                object.__setattr__(
                    self,
                    "resolved_by_check_event_id",
                    event_id(self.resolved_by_check_event_id),
                )
            except ValueError as exc:
                raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class EvidenceProjectionRecord(ProjectionRecord[EvidenceRecordedPayload]):
    object_available: bool = True
    redacted_object_id: ObjectId | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.payload is not None and type(self.payload) is not EvidenceRecordedPayload:
            raise _invalid()
        if type(self.object_available) is not bool:
            raise _invalid()
        if self.redacted_object_id is not None:
            try:
                object.__setattr__(
                    self,
                    "redacted_object_id",
                    object_id(self.redacted_object_id),
                )
            except ValueError as exc:
                raise _invalid() from exc
        if self.object_available is (self.redacted_object_id is not None):
            raise _invalid()


@dataclass(frozen=True, slots=True)
class LatestTestedState:
    source_check_event_id: EventId
    subject_frontier: Frontier
    verdict: CheckVerdict
    returned_finding_ids: tuple[FindingId, ...]
    suppressed_count: int
    coverage: Coverage

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "source_check_event_id",
                event_id(self.source_check_event_id),
            )
            finding_ids = tuple(finding_id(value) for value in self.returned_finding_ids)
        except ValueError as exc:
            raise _invalid() from exc
        if type(self.subject_frontier) is not Frontier:
            raise _invalid()
        if type(self.verdict) is not CheckVerdict:
            raise _invalid()
        if not _ascii_sorted_unique(cast(tuple[str, ...], finding_ids)):
            raise _invalid()
        object.__setattr__(self, "returned_finding_ids", finding_ids)
        object.__setattr__(
            self,
            "suppressed_count",
            _nonnegative_safe_integer(self.suppressed_count),
        )
        if type(self.coverage) is not Coverage:
            raise _invalid()


@dataclass(frozen=True, slots=True)
class ContradictionKey:
    disputing_claim_id: ClaimId
    disputed_ref: ClaimId | EventId

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "disputing_claim_id",
                claim_id(self.disputing_claim_id),
            )
            object.__setattr__(
                self,
                "disputed_ref",
                _validate_claim_or_event(self.disputed_ref),
            )
        except ValueError as exc:
            raise _invalid() from exc


@dataclass(frozen=True, slots=True)
class ContradictionRecord:
    disputing_claim_id: ClaimId
    disputed_ref: ClaimId | EventId
    source_event_id: EventId
    source_frontier: int

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "disputing_claim_id",
                claim_id(self.disputing_claim_id),
            )
            object.__setattr__(
                self,
                "disputed_ref",
                _validate_claim_or_event(self.disputed_ref),
            )
            object.__setattr__(self, "source_event_id", event_id(self.source_event_id))
        except ValueError as exc:
            raise _invalid() from exc
        object.__setattr__(self, "source_frontier", _positive_frontier(self.source_frontier))


@dataclass(frozen=True, slots=True)
class ProjectionState:
    frontier: int
    head_digest: str
    plans: Mapping[int, PlanProjectionRecord]
    obligations: Mapping[ObligationId, ObligationProjectionRecord]
    decisions: Mapping[EventId, DecisionProjectionRecord]
    assignments: Mapping[EventId, ProjectionRecord[AssignmentRecordedPayload]]
    actions: Mapping[ActionId, ProjectionRecord[ActionRecordedPayload]]
    results: Mapping[ResultId, ProjectionRecord[ResultRecordedPayload]]
    evidence: Mapping[EvidenceId, EvidenceProjectionRecord]
    claims: Mapping[ClaimId, ProjectionRecord[ClaimRecordedPayload]]
    contradictions: Mapping[ContradictionKey, ContradictionRecord]
    findings: Mapping[FindingId, FindingProjectionRecord]
    responses: Mapping[FindingId, ProjectionRecord[ResponseRecordedPayload]]
    latest_tested_state: LatestTestedState | None
    freshness: LedgerFreshness
    unknown_event_count: int
    coverage_gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.frontier) is not int or not 0 <= self.frontier <= _MAX_SQLITE_SIGNED_INTEGER:
            raise _invalid()
        try:
            if self.frontier == 0:
                if type(self.head_digest) is not str or self.head_digest != "genesis":
                    raise _invalid()
            else:
                validate_sha256_digest(self.head_digest)
        except ValueError as exc:
            raise _invalid() from exc
        if type(self.freshness) is not LedgerFreshness:
            raise _invalid()
        object.__setattr__(
            self,
            "unknown_event_count",
            _nonnegative_safe_integer(self.unknown_event_count),
        )
        if type(self.coverage_gaps) is not tuple or len(self.coverage_gaps) > _MAX_COVERAGE_GAPS:
            raise _invalid()
        gaps = tuple(_validate_gap(marker) for marker in self.coverage_gaps)
        if not _ascii_sorted_unique(gaps):
            raise _invalid()
        if self.unknown_event_count != sum(marker.startswith("unknown_event:") for marker in gaps):
            raise _invalid()
        object.__setattr__(self, "coverage_gaps", gaps)
        if self.latest_tested_state is not None:
            if (
                type(self.latest_tested_state) is not LatestTestedState
                or self.latest_tested_state.subject_frontier.sequence > self.frontier
            ):
                raise _invalid()

        plans = self._copy_plans(self.plans)
        obligations = self._copy_mapping(
            self.obligations,
            obligation_id,
            ObligationProjectionRecord,
            ObligationPublishedPayload,
            "obligation_id",
        )
        decisions = self._copy_event_mapping(
            self.decisions,
            DecisionProjectionRecord,
            DecisionRecordedPayload,
            source_key=True,
        )
        assignments = self._copy_event_mapping(
            self.assignments,
            ProjectionRecord,
            AssignmentRecordedPayload,
            source_key=True,
        )
        actions = self._copy_mapping(
            self.actions,
            action_id,
            ProjectionRecord,
            ActionRecordedPayload,
            "action_id",
        )
        results = self._copy_mapping(
            self.results,
            result_id,
            ProjectionRecord,
            ResultRecordedPayload,
            "result_id",
        )
        evidence = self._copy_mapping(
            self.evidence,
            evidence_id,
            EvidenceProjectionRecord,
            EvidenceRecordedPayload,
            "evidence_id",
        )
        claims = self._copy_mapping(
            self.claims,
            claim_id,
            ProjectionRecord,
            ClaimRecordedPayload,
            "claim_id",
        )
        findings = self._copy_mapping(
            self.findings,
            finding_id,
            FindingProjectionRecord,
            Finding,
            "finding_id",
        )
        responses = self._copy_mapping(
            self.responses,
            finding_id,
            ProjectionRecord,
            ResponseRecordedPayload,
            "finding_id",
        )
        contradictions = self._copy_contradictions(self.contradictions)

        object.__setattr__(self, "plans", MappingProxyType(plans))
        object.__setattr__(self, "obligations", MappingProxyType(obligations))
        object.__setattr__(self, "decisions", MappingProxyType(decisions))
        object.__setattr__(self, "assignments", MappingProxyType(assignments))
        object.__setattr__(self, "actions", MappingProxyType(actions))
        object.__setattr__(self, "results", MappingProxyType(results))
        object.__setattr__(self, "evidence", MappingProxyType(evidence))
        object.__setattr__(self, "claims", MappingProxyType(claims))
        object.__setattr__(self, "contradictions", MappingProxyType(contradictions))
        object.__setattr__(self, "findings", MappingProxyType(findings))
        object.__setattr__(self, "responses", MappingProxyType(responses))

    def _validate_record(self, record: _ProjectionRecordLike) -> None:
        if record.source_frontier > self.frontier:
            raise _invalid()
        if record.payload is not None:
            try:
                digest = canonical_digest(encode_payload(cast(EventPayload, record.payload)))
            except ValueError as exc:
                raise _invalid() from exc
            if digest != record.payload_digest:
                raise _invalid()

    def _copy_plans(
        self,
        source: Mapping[int, PlanProjectionRecord],
    ) -> dict[int, PlanProjectionRecord]:
        if not isinstance(cast(object, source), Mapping):
            raise _invalid()
        result: dict[int, PlanProjectionRecord] = {}
        for key, record in source.items():
            if type(key) is not int or not 1 <= key <= _MAX_SAFE_INTEGER:
                raise _invalid()
            if type(record) is not PlanProjectionRecord:
                raise _invalid()
            self._validate_record(record)
            if record.payload is not None and record.payload.plan_version != key:
                raise _invalid()
            result[key] = record
        return result

    def _copy_event_mapping[T: _ProjectionRecordLike](
        self,
        source: Mapping[EventId, T],
        record_type: type[object],
        payload_type: type[object],
        *,
        source_key: bool,
    ) -> dict[EventId, T]:
        if not isinstance(cast(object, source), Mapping):
            raise _invalid()
        result: dict[EventId, T] = {}
        for raw_key, record in source.items():
            try:
                key = event_id(raw_key)
            except ValueError as exc:
                raise _invalid() from exc
            if type(record) is not record_type:
                raise _invalid()
            self._validate_record(record)
            if record.payload is not None and type(record.payload) is not payload_type:
                raise _invalid()
            if source_key and record.source_event_id != key:
                raise _invalid()
            result[key] = record
        return result

    def _copy_mapping[K: str, T: _ProjectionRecordLike](
        self,
        source: Mapping[K, T],
        key_validator: Callable[[object], K],
        record_type: type[object],
        payload_type: type[object],
        payload_key: str,
    ) -> dict[K, T]:
        if not isinstance(cast(object, source), Mapping):
            raise _invalid()
        result: dict[K, T] = {}
        for raw_key, record in source.items():
            try:
                key = key_validator(raw_key)
            except ValueError as exc:
                raise _invalid() from exc
            if type(record) is not record_type:
                raise _invalid()
            self._validate_record(record)
            if record.payload is not None:
                if type(record.payload) is not payload_type:
                    raise _invalid()
                if getattr(record.payload, payload_key) != key:
                    raise _invalid()
            result[key] = record
        return result

    def _copy_contradictions(
        self,
        source: Mapping[ContradictionKey, ContradictionRecord],
    ) -> dict[ContradictionKey, ContradictionRecord]:
        if not isinstance(cast(object, source), Mapping):
            raise _invalid()
        result: dict[ContradictionKey, ContradictionRecord] = {}
        for key, record in source.items():
            if type(key) is not ContradictionKey or type(record) is not ContradictionRecord:
                raise _invalid()
            if (
                key.disputing_claim_id != record.disputing_claim_id
                or key.disputed_ref != record.disputed_ref
                or record.source_frontier > self.frontier
            ):
                raise _invalid()
            result[key] = record
        return result


def empty_projection_state() -> ProjectionState:
    """Return the exact generation-1 genesis projection."""

    return ProjectionState(
        frontier=0,
        head_digest="genesis",
        plans={},
        obligations={},
        decisions={},
        assignments={},
        actions={},
        results={},
        evidence={},
        claims={},
        contradictions={},
        findings={},
        responses={},
        latest_tested_state=None,
        freshness=LedgerFreshness.UNKNOWN,
        unknown_event_count=0,
        coverage_gaps=(),
    )


def unanswered_finding_count(state: ProjectionState) -> int:
    """Count findings the ledger still carries no response for.

    Any recorded response answers the finding for this counter, whatever its stance: a rejection,
    waiver, or provenance dispute answers the finding on the record, and its own quality surfaces
    as a later finding. This counter does not set the receipt's ``resolved`` field, which remains
    false for every response disposition.
    """

    return sum(key not in state.responses for key in state.findings)


def _record_snapshot(record: _ProjectionRecordLike) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {
        "payload": (
            None
            if record.payload is None
            else cast(JsonValue, encode_payload(cast(EventPayload, record.payload)))
        ),
        "payload_digest": record.payload_digest,
        "redacted": record.redacted,
        "source_event_id": record.source_event_id,
        "source_frontier": canonical_integer_string(record.source_frontier),
    }
    if type(record) is PlanProjectionRecord:
        if record.superseded_by_plan_version is not None:
            result["superseded_by_plan_version"] = canonical_integer_string(
                record.superseded_by_plan_version
            )
    elif type(record) is ObligationProjectionRecord:
        if record.plan_change is not None:
            result["plan_change"] = record.plan_change.value
            if record.plan_change_reason is not None:
                result["plan_change_reason"] = record.plan_change_reason
            result["superseded_by_obligation_ids"] = list(record.superseded_by_obligation_ids)
    elif type(record) is DecisionProjectionRecord:
        if record.superseded_by_event_id is not None:
            result["superseded_by_event_id"] = record.superseded_by_event_id
    elif type(record) is EvidenceProjectionRecord:
        result["object_available"] = record.object_available
        if record.redacted_object_id is not None:
            result["redacted_object_id"] = record.redacted_object_id
    elif type(record) is FindingProjectionRecord:
        # Emitted only when set, so every snapshot of a task whose findings were never resolved
        # stays byte-identical to the generation-1 shape frozen before proof-based resolution.
        if record.resolved_by_check_event_id is not None:
            result["resolved_by_check_event_id"] = record.resolved_by_check_event_id
    return result


def _latest_tested_snapshot(value: LatestTestedState) -> dict[str, JsonValue]:
    return {
        "source_check_event_id": value.source_check_event_id,
        "subject_frontier": value.subject_frontier.as_wire(),
        "verdict": value.verdict.value,
        "returned_finding_ids": list(value.returned_finding_ids),
        "suppressed_count": value.suppressed_count,
        "coverage": coverage_to_json(value.coverage),
    }


def _string_key(value: object) -> str:
    return str(value)


def _plan_key(value: int) -> str:
    return canonical_integer_string(value)


def _sorted_record_map[K](
    source: Mapping[K, _ProjectionRecordLike],
    *,
    key_text: Callable[[K], str] = _string_key,
) -> dict[str, JsonValue]:
    rows = sorted(
        ((key_text(key), record) for key, record in source.items()),
        key=lambda item: item[0].encode("ascii"),
    )
    return {key: _record_snapshot(record) for key, record in rows}


def projection_snapshot(state: ProjectionState) -> dict[str, JsonValue]:
    """Return the exact canonical JSON-compatible generation-1 snapshot."""

    if type(state) is not ProjectionState:
        raise _invalid()
    contradictions = {
        f"{key.disputing_claim_id}|{key.disputed_ref}": {
            "disputing_claim_id": record.disputing_claim_id,
            "disputed_ref": record.disputed_ref,
            "source_event_id": record.source_event_id,
            "source_frontier": canonical_integer_string(record.source_frontier),
        }
        for key, record in sorted(
            state.contradictions.items(),
            key=lambda item: (f"{item[0].disputing_claim_id}|{item[0].disputed_ref}").encode(
                "ascii"
            ),
        )
    }
    return {
        "frontier": canonical_integer_string(state.frontier),
        "head_digest": state.head_digest,
        "plans": _sorted_record_map(
            cast(Mapping[int, _ProjectionRecordLike], state.plans),
            key_text=_plan_key,
        ),
        "obligations": _sorted_record_map(
            cast(Mapping[ObligationId, _ProjectionRecordLike], state.obligations)
        ),
        "decisions": _sorted_record_map(
            cast(Mapping[EventId, _ProjectionRecordLike], state.decisions)
        ),
        "assignments": _sorted_record_map(
            cast(Mapping[EventId, _ProjectionRecordLike], state.assignments)
        ),
        "actions": _sorted_record_map(
            cast(Mapping[ActionId, _ProjectionRecordLike], state.actions)
        ),
        "results": _sorted_record_map(
            cast(Mapping[ResultId, _ProjectionRecordLike], state.results)
        ),
        "evidence": _sorted_record_map(
            cast(Mapping[EvidenceId, _ProjectionRecordLike], state.evidence)
        ),
        "claims": _sorted_record_map(cast(Mapping[ClaimId, _ProjectionRecordLike], state.claims)),
        "contradictions": contradictions,
        "findings": _sorted_record_map(
            cast(Mapping[FindingId, _ProjectionRecordLike], state.findings)
        ),
        "responses": _sorted_record_map(
            cast(Mapping[FindingId, _ProjectionRecordLike], state.responses)
        ),
        "latest_tested_state": (
            None
            if state.latest_tested_state is None
            else _latest_tested_snapshot(state.latest_tested_state)
        ),
        "freshness": state.freshness.value,
        "unknown_event_count": state.unknown_event_count,
        "coverage_gaps": list(state.coverage_gaps),
    }


_SNAPSHOT_KEYS: Final = frozenset(
    {
        "frontier",
        "head_digest",
        "plans",
        "obligations",
        "decisions",
        "assignments",
        "actions",
        "results",
        "evidence",
        "claims",
        "contradictions",
        "findings",
        "responses",
        "latest_tested_state",
        "freshness",
        "unknown_event_count",
        "coverage_gaps",
    }
)
_RECORD_KEYS: Final = frozenset(
    {"payload", "payload_digest", "redacted", "source_event_id", "source_frontier"}
)
_COLLECTION_SCHEMAS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "plans": ("plan_published", "plan_revised"),
        "obligations": ("obligation_published",),
        "decisions": ("decision_recorded",),
        "assignments": ("assignment_recorded",),
        "actions": ("action_recorded",),
        "results": ("result_recorded",),
        "evidence": ("evidence_recorded",),
        "claims": ("claim_recorded",),
        "findings": ("finding_recorded",),
        "responses": ("response_recorded",),
    }
)


def _snapshot_object(
    value: object,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise _invalid()
    source = cast(Mapping[object, object], value)
    keys = tuple(source)
    if any(type(key) is not str for key in keys):
        raise _invalid()
    typed = cast(Mapping[str, JsonValue], source)
    key_set = frozenset(cast(tuple[str, ...], keys))
    if not required <= key_set or key_set - required - optional:
        raise _invalid()
    return typed


def _snapshot_map(value: object) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise _invalid()
    source = cast(Mapping[object, object], value)
    keys = tuple(source)
    if any(type(key) is not str for key in keys):
        raise _invalid()
    return cast(Mapping[str, JsonValue], source)


def _snapshot_array(value: object) -> tuple[JsonValue, ...]:
    if type(value) is not tuple:
        raise _invalid()
    return cast(tuple[JsonValue, ...], value)


def _snapshot_uint(value: object, *, safe: bool = False) -> int:
    parsed = parse_canonical_integer_string(cast(str, value))
    if safe and parsed > _MAX_SAFE_INTEGER:
        raise _invalid()
    return parsed


def _schema_versions(schema_name: str) -> tuple[str, ...]:
    """Every known wire version of one schema name, newest first."""

    versions = sorted(
        {schema.version for schema in PAYLOAD_TYPES if schema.name == schema_name},
        key=lambda version: tuple(int(part) for part in version.split(".")),
        reverse=True,
    )
    if not versions:
        raise _invalid()
    return tuple(versions)


def _decoded_payload(value: JsonValue, schemas: tuple[str, ...]) -> EventPayload:
    # A snapshot record carries no schema version: generation-1 snapshots were minted when every
    # family had exactly one, and adding the version to the record would change the canonical
    # bytes every stored case_digest and resume pointer is bound to. Decoding with a pinned
    # "1.0.0" therefore rejected any payload a later version admits — an evidence record with a
    # ``digest_binding`` (evidence_recorded/1.1.0) failed as ``unknown_payload_field``, which
    # turned every deferred rehydration of a frozen case (privacy-wait replay, ledger recovery)
    # into a non-retryable STORAGE_CORRUPT on an intact bundle (issue #427). Each name decodes
    # under its newest admitting version instead; the name ambiguity check is unchanged.
    decoded: list[EventPayload] = []
    for schema_name in schemas:
        for version in _schema_versions(schema_name):
            try:
                payload = decode_payload(
                    EventSchema(schema_name, version),
                    cast(DomainJsonValue, value),
                )
            except ValueError:
                continue
            decoded.append(payload)
            break
    if len(decoded) != 1:
        raise _invalid()
    return decoded[0]


def _record_from_snapshot(
    value: JsonValue,
    *,
    collection: str,
) -> _ProjectionRecordLike:
    optional: frozenset[str]
    required = _RECORD_KEYS
    if collection == "plans":
        optional = frozenset({"superseded_by_plan_version"})
    elif collection == "obligations":
        optional = frozenset(
            {
                "plan_change",
                "plan_change_reason",
                "superseded_by_obligation_ids",
            }
        )
    elif collection == "decisions":
        optional = frozenset({"superseded_by_event_id"})
    elif collection == "evidence":
        required = _RECORD_KEYS | frozenset({"object_available"})
        optional = frozenset({"redacted_object_id"})
    elif collection == "findings":
        optional = frozenset({"resolved_by_check_event_id"})
    else:
        optional = frozenset()
    source = _snapshot_object(value, required=required, optional=optional)
    raw_payload = source["payload"]
    payload = (
        None
        if raw_payload is None
        else _decoded_payload(raw_payload, _COLLECTION_SCHEMAS[collection])
    )
    payload_digest = source["payload_digest"]
    redacted = source["redacted"]
    if type(payload_digest) is not str or type(redacted) is not bool:
        raise _invalid()
    source_event_id = event_id(source["source_event_id"])
    source_frontier = _snapshot_uint(source["source_frontier"])
    if collection == "plans":
        raw_superseded = source.get("superseded_by_plan_version")
        if "superseded_by_plan_version" in source and raw_superseded is None:
            raise _invalid()
        return PlanProjectionRecord(
            payload=cast(PlanPublishedPayload | PlanRevisedPayload | None, payload),
            payload_digest=payload_digest,
            redacted=redacted,
            source_event_id=source_event_id,
            source_frontier=source_frontier,
            superseded_by_plan_version=(
                None if raw_superseded is None else _snapshot_uint(raw_superseded, safe=True)
            ),
        )
    if collection == "obligations":
        has_change = "plan_change" in source
        if has_change is not ("superseded_by_obligation_ids" in source):
            raise _invalid()
        if not has_change and "plan_change_reason" in source:
            raise _invalid()
        if "plan_change_reason" in source and source["plan_change_reason"] is None:
            raise _invalid()
        raw_replacements = source.get("superseded_by_obligation_ids", ())
        return ObligationProjectionRecord(
            payload=cast(ObligationPublishedPayload | None, payload),
            payload_digest=payload_digest,
            redacted=redacted,
            source_event_id=source_event_id,
            source_frontier=source_frontier,
            plan_change=(
                None if not has_change else ObligationChangeKind(cast(str, source["plan_change"]))
            ),
            plan_change_reason=cast(str | None, source.get("plan_change_reason")),
            superseded_by_obligation_ids=cast(
                tuple[ObligationId, ...],
                _snapshot_array(raw_replacements),
            ),
        )
    if collection == "decisions":
        if "superseded_by_event_id" in source and source["superseded_by_event_id"] is None:
            raise _invalid()
        return DecisionProjectionRecord(
            payload=cast(DecisionRecordedPayload | None, payload),
            payload_digest=payload_digest,
            redacted=redacted,
            source_event_id=source_event_id,
            source_frontier=source_frontier,
            superseded_by_event_id=cast(EventId | None, source.get("superseded_by_event_id")),
        )
    if collection == "evidence":
        if "redacted_object_id" in source and source["redacted_object_id"] is None:
            raise _invalid()
        return EvidenceProjectionRecord(
            payload=cast(EvidenceRecordedPayload | None, payload),
            payload_digest=payload_digest,
            redacted=redacted,
            source_event_id=source_event_id,
            source_frontier=source_frontier,
            object_available=cast(bool, source["object_available"]),
            redacted_object_id=cast(ObjectId | None, source.get("redacted_object_id")),
        )
    if collection == "findings":
        if "resolved_by_check_event_id" in source and source["resolved_by_check_event_id"] is None:
            raise _invalid()
        return FindingProjectionRecord(
            payload=cast(Finding | None, payload),
            payload_digest=payload_digest,
            redacted=redacted,
            source_event_id=source_event_id,
            source_frontier=source_frontier,
            resolved_by_check_event_id=cast(
                EventId | None, source.get("resolved_by_check_event_id")
            ),
        )
    return ProjectionRecord(
        payload=payload,
        payload_digest=payload_digest,
        redacted=redacted,
        source_event_id=source_event_id,
        source_frontier=source_frontier,
    )


def _record_map_from_snapshot(
    value: JsonValue,
    *,
    collection: str,
) -> dict[str, _ProjectionRecordLike]:
    source = _snapshot_map(value)
    return {
        key: _record_from_snapshot(record, collection=collection) for key, record in source.items()
    }


def _plans_from_snapshot(value: JsonValue) -> dict[int, PlanProjectionRecord]:
    decoded = _record_map_from_snapshot(value, collection="plans")
    return {
        _snapshot_uint(key, safe=True): cast(PlanProjectionRecord, record)
        for key, record in decoded.items()
    }


def _latest_tested_from_snapshot(value: JsonValue) -> LatestTestedState | None:
    if value is None:
        return None
    source = _snapshot_object(
        value,
        required=frozenset(
            {
                "source_check_event_id",
                "subject_frontier",
                "verdict",
                "returned_finding_ids",
                "suppressed_count",
                "coverage",
            }
        ),
    )
    return LatestTestedState(
        source_check_event_id=cast(EventId, source["source_check_event_id"]),
        subject_frontier=frontier_from_json(source["subject_frontier"]),
        verdict=CheckVerdict(cast(str, source["verdict"])),
        returned_finding_ids=cast(
            tuple[FindingId, ...],
            _snapshot_array(source["returned_finding_ids"]),
        ),
        suppressed_count=cast(int, source["suppressed_count"]),
        coverage=coverage_from_json(source["coverage"]),
    )


def _contradictions_from_snapshot(
    value: JsonValue,
) -> dict[ContradictionKey, ContradictionRecord]:
    source = _snapshot_map(value)
    result: dict[ContradictionKey, ContradictionRecord] = {}
    for encoded_key, raw_record in source.items():
        parts = encoded_key.split("|")
        if len(parts) != 2:
            raise _invalid()
        record_source = _snapshot_object(
            raw_record,
            required=frozenset(
                {
                    "disputing_claim_id",
                    "disputed_ref",
                    "source_event_id",
                    "source_frontier",
                }
            ),
        )
        if parts != [record_source["disputing_claim_id"], record_source["disputed_ref"]]:
            raise _invalid()
        key = ContradictionKey(
            disputing_claim_id=cast(ClaimId, record_source["disputing_claim_id"]),
            disputed_ref=cast(ClaimId | EventId, record_source["disputed_ref"]),
        )
        result[key] = ContradictionRecord(
            disputing_claim_id=key.disputing_claim_id,
            disputed_ref=key.disputed_ref,
            source_event_id=cast(EventId, record_source["source_event_id"]),
            source_frontier=_snapshot_uint(record_source["source_frontier"]),
        )
    return result


def projection_from_snapshot(value: JsonValue) -> ProjectionState:
    """Decode and revalidate one exact generation-1 projection snapshot."""

    try:
        frozen = freeze_json(value)
        source = _snapshot_object(frozen, required=_SNAPSHOT_KEYS)
        freshness_value = source["freshness"]
        if type(freshness_value) is not str:
            raise _invalid()
        return ProjectionState(
            frontier=_snapshot_uint(source["frontier"]),
            head_digest=cast(str, source["head_digest"]),
            plans=_plans_from_snapshot(source["plans"]),
            obligations=cast(
                Mapping[ObligationId, ObligationProjectionRecord],
                _record_map_from_snapshot(source["obligations"], collection="obligations"),
            ),
            decisions=cast(
                Mapping[EventId, DecisionProjectionRecord],
                _record_map_from_snapshot(source["decisions"], collection="decisions"),
            ),
            assignments=cast(
                Mapping[EventId, ProjectionRecord[AssignmentRecordedPayload]],
                _record_map_from_snapshot(source["assignments"], collection="assignments"),
            ),
            actions=cast(
                Mapping[ActionId, ProjectionRecord[ActionRecordedPayload]],
                _record_map_from_snapshot(source["actions"], collection="actions"),
            ),
            results=cast(
                Mapping[ResultId, ProjectionRecord[ResultRecordedPayload]],
                _record_map_from_snapshot(source["results"], collection="results"),
            ),
            evidence=cast(
                Mapping[EvidenceId, EvidenceProjectionRecord],
                _record_map_from_snapshot(source["evidence"], collection="evidence"),
            ),
            claims=cast(
                Mapping[ClaimId, ProjectionRecord[ClaimRecordedPayload]],
                _record_map_from_snapshot(source["claims"], collection="claims"),
            ),
            contradictions=_contradictions_from_snapshot(source["contradictions"]),
            findings=cast(
                Mapping[FindingId, FindingProjectionRecord],
                _record_map_from_snapshot(source["findings"], collection="findings"),
            ),
            responses=cast(
                Mapping[FindingId, ProjectionRecord[ResponseRecordedPayload]],
                _record_map_from_snapshot(source["responses"], collection="responses"),
            ),
            latest_tested_state=_latest_tested_from_snapshot(source["latest_tested_state"]),
            freshness=LedgerFreshness(freshness_value),
            unknown_event_count=cast(int, source["unknown_event_count"]),
            coverage_gaps=cast(tuple[str, ...], _snapshot_array(source["coverage_gaps"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if type(exc) is ValueError and str(exc) == "invalid_projection_state":
            raise
        raise _invalid() from exc


def projection_digest(state: ProjectionState) -> str:
    """Digest the exact canonical projection snapshot."""

    return canonical_digest(projection_snapshot(state))
