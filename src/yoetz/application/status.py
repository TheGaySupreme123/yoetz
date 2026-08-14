"""Bounded read-only status projections at an authenticated stable frontier."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

from pydantic import BaseModel

from yoetz.application.check import CheckScope, run_deterministic_policies
from yoetz.domain.findings import FINDING_KIND_TRAITS, FindingOrigin
from yoetz.domain.observation import AdviceSnapshot
from yoetz.domain.values import (
    Frontier,
    SemanticContinuation,
    disclosure_continuation,
    repository_grant_continuation,
)
from yoetz.kernel.deterministic_checks import (
    DeterministicAssessment,
    build_deterministic_case,
    finding_basis_to_status_json,
)
from yoetz.observability.logging import record_unexpected_exception_without_raising
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ledger import (
    AssignmentProjectionFilter,
    CheckSuspensionKind,
    EvidenceProjectionFilter,
    FindingProjectionPosition,
    FindingsProjectionFilter,
    HistoryProjectionFilter,
    HistoryProjectionPosition,
    IdProjectionPosition,
    ObligationsProjectionFilter,
    OperationKind,
    OperationRecord,
    OperationState,
    ProjectionFilter,
    ProjectionPage,
    ProjectionPosition,
    ProjectionQuery,
    ProjectionView,
)
from yoetz.ports.runtime import BundleRuntimePort, RouteAccess, RouteCommand, TaskRuntime
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.coverage import (
    ARTIFACT_OBSERVATION_ORDER,
    AUTHORSHIP_ASSURANCE_ORDER,
    EVIDENCE_IMMUTABILITY_ORDER,
    LEDGER_FRESHNESS_ORDER,
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
    coverage_to_json,
)
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    StatusAdvicePageModel,
    StatusAssignmentFilterModel,
    StatusAssignmentPageModel,
    StatusCandidateFindingItemModel,
    StatusCandidateFindingsFilterModel,
    StatusCandidateFindingsPageModel,
    StatusClosureReadinessModel,
    StatusCompactItemModel,
    StatusCompactPageModel,
    StatusEvidenceFilterModel,
    StatusEvidencePageModel,
    StatusFilter,
    StatusFindingsFilterModel,
    StatusFindingsPageModel,
    StatusHistoryFilterModel,
    StatusHistoryPageModel,
    StatusImportStatusModel,
    StatusObligationsFilterModel,
    StatusObligationsPageModel,
    StatusOperationFilterModel,
    StatusOperationPageModel,
    StatusPage,
    StatusRequest,
    StatusVersionSliceModel,
    StatusVersionsPageModel,
)

__all__ = ["Application", "StatusInternalResult", "execute_status"]

_PACKS = ("research-evidence/0.1.0", "work-integrity/0.1.0")
_CURSOR_VERSION = "1"


def _dump_closed_omitting_optional_nulls(model: BaseModel) -> dict[str, JsonValue]:
    """Dump one closed model tree for the internal status body.

    Required nullable leaves (for example ``revision_event_id`` and ``next_cursor``) stay present
    as JSON null. Fields declared in ``optional_non_null_fields`` that are unset are omitted
    entirely — never reintroduced as null — so the closed wire models accept the body.
    """

    dumped = cast(dict[str, JsonValue], model.model_dump(mode="json", exclude_none=False))
    return _strip_optional_non_null_nulls(model, dumped)


def _strip_optional_non_null_nulls(
    model: BaseModel, dumped: Mapping[str, JsonValue]
) -> dict[str, JsonValue]:
    """Drop null leaves that ``optional_non_null_fields`` forbids, recursively."""

    optional_fields: frozenset[str] = frozenset()
    declared = getattr(type(model), "optional_non_null_fields", None)
    if isinstance(declared, frozenset):
        optional_fields = cast(frozenset[str], declared)
    result: dict[str, JsonValue] = {}
    for key, value in dumped.items():
        if key in optional_fields and value is None:
            continue
        attr: object = getattr(model, key)
        if isinstance(attr, BaseModel) and isinstance(value, Mapping):
            result[key] = cast(
                JsonValue,
                _strip_optional_non_null_nulls(attr, cast(Mapping[str, JsonValue], value)),
            )
            continue
        if (
            isinstance(attr, Sequence)
            and not isinstance(attr, (str, bytes))
            and isinstance(value, list)
        ):
            children = cast(Sequence[object], attr)
            items: list[JsonValue] = []
            for child, child_dump in zip(children, cast(list[JsonValue], value), strict=True):
                if isinstance(child, BaseModel) and isinstance(child_dump, Mapping):
                    items.append(
                        cast(
                            JsonValue,
                            _strip_optional_non_null_nulls(
                                child, cast(Mapping[str, JsonValue], child_dump)
                            ),
                        )
                    )
                else:
                    items.append(child_dump)
            result[key] = items
            continue
        result[key] = value
    return result


class Application(Protocol):
    runtime: BundleRuntimePort
    status_cursor_key: bytes


@dataclass(frozen=True, slots=True)
class StatusInternalResult:
    protocol_version: str
    schema_version: str
    request_id: str
    ok: bool
    task_id: str
    session_id: str
    writer_id: str
    view: str
    requested_frontier: Frontier
    head_frontier: Frontier
    subject_frontier: Frontier
    result_frontier: Frontier
    projection_lag: int
    projection_version: str
    rebuild_state: str
    page: StatusPage
    coverage: Coverage
    gaps: tuple[str, ...]
    import_status: StatusImportStatusModel
    closure_readiness: StatusClosureReadinessModel

    def as_json(self) -> dict[str, JsonValue]:
        # Unset optional non-null leaves (today: obligation ``acceptance_criteria``, structural
        # subject-state digests) must be entirely absent from the internal body, never present as
        # explicit null. A blanket ``exclude_none`` would also drop required nullable keys such as
        # ``revision_event_id`` and ``next_cursor``; a blanket ``exclude_unset`` drops defaults
        # that the frozen schema still requires as null. The page dump therefore strips only the
        # fields each closed model lists in ``optional_non_null_fields``.
        return {
            "protocol_version": self.protocol_version,
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "ok": self.ok,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "writer_id": self.writer_id,
            "view": self.view,
            "requested_frontier": dict(self.requested_frontier.as_wire().items()),
            "head_frontier": dict(self.head_frontier.as_wire().items()),
            "subject_frontier": dict(self.subject_frontier.as_wire().items()),
            "result_frontier": dict(self.result_frontier.as_wire().items()),
            "projection_lag": str(self.projection_lag),
            "projection_version": self.projection_version,
            "rebuild_state": self.rebuild_state,
            "page": cast(JsonValue, _dump_closed_omitting_optional_nulls(self.page)),
            "coverage": coverage_to_json(self.coverage),
            "gaps": self.gaps,
            "import_status": cast(
                JsonValue, _dump_closed_omitting_optional_nulls(self.import_status)
            ),
            "closure_readiness": cast(
                JsonValue, _dump_closed_omitting_optional_nulls(self.closure_readiness)
            ),
        }


def _error(code: PublicErrorCode, message: str) -> PublicOperationError:
    return PublicOperationError(code, message, False)


def _filter_json(value: StatusFilter | None) -> JsonValue:
    if value is None:
        return None
    return cast(JsonValue, value.model_dump(mode="json", exclude_none=False))


def _filter_digest(request: StatusRequest) -> str:
    return canonical_digest(_filter_json(request.filter))


# A SHA-256 HMAC digest is always exactly 32 bytes, so its unpadded base64url encoding is
# always exactly this many characters -- fixed regardless of content. This lets the cursor
# encode body and signature back-to-back with no separator character, matching the frozen
# wire ``CursorWire`` pattern (``^[A-Za-z0-9_-]+$``), which admits no ``.`` or other delimiter.
_SIGNATURE_B64_LEN: Final = 43


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.") from exc


def _position_json(position: ProjectionPosition | int | None) -> JsonValue:
    if position is None:
        return None
    if type(position) is int:
        return {"kind": "candidate", "offset": str(position)}
    if type(position) is IdProjectionPosition:
        return {"kind": "id", "last_id": position.last_id}
    if type(position) is HistoryProjectionPosition:
        return {"kind": "history", "sequence": str(position.ingestion_sequence)}
    assert type(position) is FindingProjectionPosition
    return {
        "kind": "finding",
        "priority": position.priority,
        "actionable": position.actionable,
        "artifact_ordinal": position.artifact_ordinal,
        "immutability_ordinal": position.immutability_ordinal,
        "freshness_ordinal": position.freshness_ordinal,
        "authorship_ordinal": position.authorship_ordinal,
        "real_check_present": position.real_check_present,
        "known_gap_count": position.known_gap_count,
        "origin_ordinal": position.origin_ordinal,
        "finding_id": position.finding_id,
    }


def _position_from_json(value: object) -> ProjectionPosition | int | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise ValueError("cursor_position_invalid")
    source = cast(dict[str, object], value)
    kind = source.get("kind")
    if kind == "candidate" and frozenset(source) == frozenset({"kind", "offset"}):
        offset = int(cast(str, source["offset"]))
        if offset < 0:
            raise ValueError("cursor_position_invalid")
        return offset
    if kind == "id" and frozenset(source) == frozenset({"kind", "last_id"}):
        return IdProjectionPosition(cast(str, source["last_id"]))
    if kind == "history" and frozenset(source) == frozenset({"kind", "sequence"}):
        return HistoryProjectionPosition(int(cast(str, source["sequence"])))
    if kind == "finding":
        return FindingProjectionPosition(
            cast(int, source["priority"]),
            cast(bool, source["actionable"]),
            cast(int, source["artifact_ordinal"]),
            cast(int, source["immutability_ordinal"]),
            cast(int, source["freshness_ordinal"]),
            cast(int, source["authorship_ordinal"]),
            cast(bool, source["real_check_present"]),
            cast(int, source["known_gap_count"]),
            cast(int, source["origin_ordinal"]),
            cast(str, source["finding_id"]),
        )
    raise ValueError("cursor_position_invalid")


def _encode_cursor(
    app: Application,
    request: StatusRequest,
    frontier: Frontier,
    projection_version: str,
    position: ProjectionPosition | int,
) -> str:
    body = canonical_encode(
        {
            "v": _CURSOR_VERSION,
            "session_id": request.session_id,
            "view": request.view,
            "filter_digest": _filter_digest(request),
            "frontier": frontier.as_wire(),
            "projection_version": projection_version,
            "limit": request.limit,
            "position": _position_json(position),
        }
    )
    signature = hmac.new(app.status_cursor_key, body, hashlib.sha256).digest()
    encoded_signature = _b64(signature)
    assert len(encoded_signature) == _SIGNATURE_B64_LEN
    return f"{_b64(body)}{encoded_signature}"


def _decode_cursor(
    app: Application, request: StatusRequest
) -> tuple[Frontier, str, ProjectionPosition | int] | None:
    if request.cursor is None:
        return None
    if type(app.status_cursor_key) is not bytes or len(app.status_cursor_key) < 32:
        raise _error(PublicErrorCode.SERVICE_UNAVAILABLE, "Status pagination is unavailable.")
    try:
        if len(request.cursor) <= _SIGNATURE_B64_LEN:
            raise ValueError("cursor_shape_invalid")
        body_part = request.cursor[:-_SIGNATURE_B64_LEN]
        signature_part = request.cursor[-_SIGNATURE_B64_LEN:]
        body = _unb64(body_part)
        signature = _unb64(signature_part)
        expected = hmac.new(app.status_cursor_key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("cursor_signature_invalid")
        raw = strict_json_parse(body)
        if type(raw) is not dict:
            raise ValueError("cursor_shape_invalid")
        source = cast(dict[str, object], raw)
        if (
            source.get("v") != _CURSOR_VERSION
            or source.get("session_id") != request.session_id
            or source.get("view") != request.view
            or source.get("filter_digest") != _filter_digest(request)
            or source.get("limit") != request.limit
        ):
            raise ValueError("cursor_binding_invalid")
        frontier_raw = cast(dict[str, object], source["frontier"])
        frontier = Frontier(
            int(cast(str, frontier_raw["sequence"])),
            cast(str, frontier_raw["head_digest"]),
        )
        position = _position_from_json(source["position"])
        if position is None:
            raise ValueError("cursor_position_invalid")
        return frontier, cast(str, source["projection_version"]), position
    except PublicOperationError:
        raise
    except Exception as exc:
        raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.") from exc


def _port_filter(value: StatusFilter | None) -> ProjectionFilter | None:
    if value is None:
        return None
    if type(value) is StatusAssignmentFilterModel:
        return AssignmentProjectionFilter(value.actor_id, value.include_resolved)
    if type(value) is StatusObligationsFilterModel:
        return ObligationsProjectionFilter(value.actor_id, value.include_resolved, value.status)
    if type(value) is StatusFindingsFilterModel:
        return FindingsProjectionFilter(
            value.origin, value.priority, value.disposition, value.include_resolved
        )
    if type(value) is StatusEvidenceFilterModel:
        return EvidenceProjectionFilter(value.strength, value.freshness, value.include_unavailable)
    if type(value) is StatusHistoryFilterModel:
        return HistoryProjectionFilter(
            value.schema_name,
            value.actor_id,
            None if value.after_sequence is None else int(value.after_sequence),
        )
    if type(value) is StatusOperationFilterModel:
        # Operation recovery is ledger-operation keyed, not a projection row query.
        raise ValueError("status_filter_invalid")
    raise ValueError("status_filter_invalid")


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    if type(value) is not dict:
        raise ValueError("status_operation_result_invalid")
    source = cast(dict[str, object], value)
    if any(type(key) is not str for key in source):
        raise ValueError("status_operation_result_invalid")
    return cast(dict[str, JsonValue], source)


async def _operation_continuation(
    application: Application,
    writer_id: str,
    operation: OperationRecord,
) -> dict[str, JsonValue] | None:
    """Rebuild the continuation for one suspended check from durable state.

    Recovery must never depend on the caller having kept the original result, and it must never
    require reading Yoetz's storage by hand. A ledger without the wait API, or any fault reading
    it, simply yields no continuation: recovery is best-effort enrichment and must not turn a
    readable operation page into an error.
    """

    runtime = application.runtime
    operation_request_id = operation.operation_id
    ledger = getattr(runtime, "ledger", None)
    load = getattr(ledger, "load_disclosure_wait", None)
    continuation: SemanticContinuation | None = None
    if operation.suspension_kind is CheckSuspensionKind.REPOSITORY_GRANT:
        continuation = repository_grant_continuation(request_id=operation_request_id)
    elif load is not None:
        try:
            wait = await load(writer_id, operation_request_id)
        except Exception:
            wait = None
        if wait is not None and getattr(wait, "state", None) == "awaiting":
            try:
                continuation = disclosure_continuation(
                    pending_id=wait.pending_id,
                    expires_at=wait.pending_expires_at,
                    request_id=operation_request_id,
                )
            except TypeError, ValueError:
                continuation = None
    if continuation is None:
        return None
    result: dict[str, JsonValue] = {
        "kind": continuation.kind,
        "command": continuation.command,
        "replay_request_id": continuation.request_id,
        "instruction": continuation.instruction,
    }
    if continuation.pending_id is not None:
        result["pending_id"] = continuation.pending_id
    if continuation.expires_at is not None:
        result["expires_at"] = continuation.expires_at.wire
    return result


def _operation_page_from_record(
    operation_request_id: str,
    operation: object | None,
    continuation: Mapping[str, JsonValue] | None = None,
) -> StatusOperationPageModel:
    """Project one operation record into the recovery page, or a bounded not-found page.

    Every exit is either a validated page or ``ValueError``. Attribute and type faults on a
    stored record (corrupt shape, missing enum members) collapse to the same bounded error so
    the operation status branch never raises an unbounded exception into the daemon.
    """

    if operation is None:
        return StatusOperationPageModel.model_validate(
            {
                "operation_request_id": operation_request_id,
                "found": False,
                "state": "absent",
            }
        )
    from yoetz.ports.ledger import OperationRecord as _OperationRecord

    try:
        if type(operation) is not _OperationRecord:
            raise ValueError("status_operation_result_invalid")
        record = operation
        kind = record.operation_kind.value
        if record.state is OperationState.PENDING:
            pending: dict[str, JsonValue] = {
                "operation_request_id": operation_request_id,
                "found": True,
                "state": "pending",
                "operation_kind": kind,
            }
            # Only a suspended check has one; it is what makes recovery possible after the
            # original result scrolled away or the context was compacted.
            if continuation is not None and kind == "check":
                pending["continuation"] = cast(JsonValue, dict(continuation))
            return StatusOperationPageModel.model_validate(pending)
        if record.state is OperationState.QUARANTINED:
            return StatusOperationPageModel.model_validate(
                {
                    "operation_request_id": operation_request_id,
                    "found": True,
                    "state": "quarantined",
                    "operation_kind": kind,
                }
            )
        if record.state is not OperationState.COMPLETE or record.result_canonical is None:
            raise ValueError("status_operation_result_invalid")
        # Only publish_work stores the AppendResult shape used here. Other complete kinds surface
        # without accepted-event detail so recovery stays bounded and honest.
        if record.operation_kind is not OperationKind.PUBLISH_WORK:
            return StatusOperationPageModel.model_validate(
                {
                    "operation_request_id": operation_request_id,
                    "found": True,
                    "state": "complete",
                    "operation_kind": kind,
                }
            )
        source = _mapping(strict_json_parse(record.result_canonical))
        accepted_raw = source["accepted"]
        if type(accepted_raw) is not tuple and type(accepted_raw) is not list:
            raise ValueError("status_operation_result_invalid")
        accepted = tuple(
            {
                "event_id": cast(str, item["event_id"]),
                "entry_digest": cast(str, item["entry_digest"]),
                "ingestion_sequence": cast(str, item["ingestion_sequence"]),
                "writer_sequence": cast(str, item["writer_sequence"]),
                "projection_status": cast(str, item["projection_status"]),
            }
            for item in (
                _mapping(cast(JsonValue, value))
                for value in cast(tuple[object, ...] | list[object], accepted_raw)
            )
        )
        subject = cast(dict[str, object], source["subject_frontier"])
        result = cast(dict[str, object], source["result_frontier"])
        return StatusOperationPageModel.model_validate(
            {
                "operation_request_id": operation_request_id,
                "found": True,
                "state": "complete",
                "operation_kind": "publish_work",
                "outcome": "accepted",
                "subject_frontier": {
                    "sequence": cast(str, subject["sequence"]),
                    "head_digest": cast(str, subject["head_digest"]),
                },
                "result_frontier": {
                    "sequence": cast(str, result["sequence"]),
                    "head_digest": cast(str, result["head_digest"]),
                },
                "accepted_events": accepted,
            }
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("status_operation_result_invalid") from exc


async def _exact_frontier(runtime: TaskRuntime, sequence: int | None) -> tuple[Frontier, Frontier]:
    stored = await runtime.ledger.load_projection(runtime.session_id, ProjectionView.COMPACT)
    if stored is None:
        head = Frontier.genesis()
    else:
        head = stored.frontier
    target = head.sequence if sequence is None else sequence
    if target > head.sequence:
        # Status is a read-only query: a requested sequence beyond the observed head is invalid
        # query input, never a stale optimistic mutation guard. See
        # See the status error mapping in docs/INTERFACES.md.
        raise _error(PublicErrorCode.INVALID_REQUEST, "The requested frontier is in the future.")
    if target == head.sequence:
        return head, head
    if target == 0:
        return Frontier.genesis(), head
    found: Frontier | None = None
    async for record in runtime.ledger.load_events(runtime.session_id, through=target):
        found = Frontier(record.ledger.ingestion_sequence, record.entry_digest)
    if found is None or found.sequence != target:
        raise PublicOperationError(
            PublicErrorCode.FRONTIER_CONFLICT,
            (
                "The requested frontier is unavailable. Call status to read the current frontier, "
                "then retry idempotently with the same request_id."
            ),
            True,
            safe_details={
                "reason_code": "frontier_changed",
                "sequence": head.sequence,
                "head_digest": head.head_digest,
            },
        )
    return found, head


def _page_model(page: ProjectionPage, next_cursor: str | None) -> StatusPage:
    value = {"items": page.items, "next_cursor": next_cursor}
    constructors = {
        "assignment": StatusAssignmentPageModel,
        "compact": StatusCompactPageModel,
        "evidence": StatusEvidencePageModel,
        "findings": StatusFindingsPageModel,
        "history": StatusHistoryPageModel,
        "obligations": StatusObligationsPageModel,
        "versions": StatusVersionsPageModel,
    }
    return constructors[page.view].model_validate(value)


def _candidate_rank(item: DeterministicAssessment, ordinal: int) -> tuple[object, ...]:
    candidate = item.candidate
    coverage = candidate.coverage
    priority, actionable = FINDING_KIND_TRAITS[candidate.kind]
    real_check = int(
        CheckType.DETERMINISTIC in coverage.check_types
        or CheckType.SEMANTIC_MODEL_DERIVED in coverage.check_types
    )
    return (
        priority,
        -int(actionable),
        -ARTIFACT_OBSERVATION_ORDER[coverage.artifact_observation],
        -EVIDENCE_IMMUTABILITY_ORDER[coverage.evidence_immutability],
        -LEDGER_FRESHNESS_ORDER[coverage.ledger_freshness],
        -AUTHORSHIP_ASSURANCE_ORDER[coverage.authorship_assurance],
        -real_check,
        len(coverage.known_gaps),
        int(candidate.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED),
        ordinal,
    )


async def _candidate_page(
    app: Application,
    request: StatusRequest,
    runtime: TaskRuntime,
    frontier: Frontier,
    head: Frontier,
    offset: int,
) -> tuple[StatusPage, Coverage, tuple[str, ...], str | None]:
    records = tuple(
        [
            record
            async for record in runtime.ledger.load_events(
                runtime.session_id, through=frontier.sequence
            )
        ]
    )
    from yoetz.kernel.reducers import replay

    try:
        projection = replay(records)
    except ValueError as exc:
        # Replay is genesis-anchored; a chain it rejects is a storage fact, not an engine bug, so
        # it leaves here as a bounded public error rather than an unbounded internal one.
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The task ledger is unreadable.") from exc
    if Frontier(projection.frontier, projection.head_digest) != frontier:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The status frontier is inconsistent.")
    availability = await runtime.ledger.load_case_availability(
        runtime.session_id, frontier, projection
    )
    try:
        case = build_deterministic_case(projection, records, availability)
    except ValueError as exc:
        if str(exc) == "deterministic_case_invalid":
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "The status case is unreadable.",
            ) from exc
        raise
    assessments, _ = run_deterministic_policies(case, CheckScope((), ()), _PACKS)
    indexed = tuple(enumerate(assessments))
    ordered = tuple(
        item
        for _, item in sorted(indexed, key=lambda row: _candidate_rank(row[1], row[0]))
        if type(request.filter) is not StatusCandidateFindingsFilterModel
        or request.filter.priority is None
        or item.candidate.priority == request.filter.priority
    )
    limit = int(request.limit)
    selected = ordered[offset : offset + limit]
    items = tuple(
        StatusCandidateFindingItemModel.model_validate(
            {
                "kind": item.candidate.kind.value,
                "origin": "deterministic",
                "priority": item.candidate.priority,
                "summary": item.candidate.summary,
                "detail": item.candidate.detail,
                "subject_refs": item.candidate.subject_refs,
                "policy_id": item.candidate.policy_id,
                "policy_version": item.candidate.policy_version,
                "subject_frontier": dict(item.candidate.subject_frontier.as_wire().items()),
                "coverage": coverage_to_json(item.candidate.coverage),
                "basis": finding_basis_to_status_json(item),
            }
        )
        for item in selected
    )
    next_offset = offset + len(selected)
    next_cursor = (
        _encode_cursor(app, request, frontier, runtime.projection_version, next_offset)
        if next_offset < len(ordered)
        else None
    )
    from yoetz.application.check import case_coverage

    coverage = case_coverage(case)
    return (
        StatusCandidateFindingsPageModel(items=items, next_cursor=next_cursor),
        coverage,
        coverage.known_gaps,
        next_cursor,
    )


async def _import_status(runtime: TaskRuntime) -> StatusImportStatusModel:
    snapshot = await runtime.importer.status(runtime.session_id)
    phase: (
        Literal[
            "plan_ready",
            "publishing",
            "report_published",
            "report_ready",
            "source_reserved",
            "terminal",
        ]
        | None
    ) = None
    report_evidence_id: str | None = None
    identity: str | None = None
    if snapshot.active_jobs:
        active = snapshot.active_jobs[0]
        phase = cast(
            Literal[
                "plan_ready",
                "publishing",
                "report_published",
                "report_ready",
                "source_reserved",
            ],
            active["phase"],
        )
        identity = cast(str, active["identity_digest"])
    elif snapshot.terminal_report_locators:
        terminal = snapshot.terminal_report_locators[0]
        phase = "terminal"
        report_evidence_id = cast(str, terminal["report_evidence_id"])
        identity = cast(str, terminal["identity_digest"])
    return StatusImportStatusModel(
        pending_count=str(snapshot.active_job_count),
        terminal_count=str(snapshot.terminal_job_count),
        phase=phase,
        report_evidence_id=report_evidence_id,
        source_identity_digest=identity,
    )


def _unknown_structural_coverage() -> Coverage:
    """Honest coverage when a structural recovery path cannot load a projection page."""

    return Coverage(
        publication_channels=(PublicationChannel.LOCAL_CLI,),
        authorship_assurance=AuthorshipAssurance.SELF_ASSERTED,
        artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
        evidence_immutability=EvidenceImmutability.METADATA_ONLY,
        ledger_freshness=LedgerFreshness.UNKNOWN,
        check_types=(CheckType.NONE,),
        known_gaps=(),
    )


def _readiness_unknown() -> StatusClosureReadinessModel:
    return StatusClosureReadinessModel(
        declared_obligation_count=None,
        no_obligations_reason=None,
        open_obligation_count=None,
        unresolved_finding_count=None,
        blocking_conditions=("readiness_unknown",),
    )


async def _closure_readiness(
    runtime: TaskRuntime,
    frontier: Frontier,
    compact_page: ProjectionPage | None = None,
    request_id: str | None = None,
) -> StatusClosureReadinessModel:
    """Derive what currently bounds a completion conclusion, from the compact projection.

    Computed on every view so an agent never has to switch views — or spend a check and a receipt
    — to learn that the record cannot yet support a completion claim. This reads only: it creates
    no task-ledger consequence and changes no coverage. Its one write is the owner-only bounded
    diagnostic below, emitted when the page it is handed has an unexpected shape.

    ``view=compact`` already loads exactly this page, so it passes it in rather than paying for a
    second identical query. Compact admits no filter or position, so the page is equivalent.

    Because it runs on *every* view, it is also the one enrichment that can strand every view at
    once. It is therefore total over the page it is handed: an unexpected page shape degrades to
    ``readiness_unknown`` — the same honest answer a lagging projection already produces — and
    leaves a bounded diagnostic, rather than raising into the daemon as an unbounded internal
    error on a read that changed nothing.
    """

    if compact_page is not None:
        page = compact_page
    else:
        try:
            page = await runtime.ledger.query_projection(
                ProjectionQuery(
                    runtime.session_id,
                    "compact",
                    None,
                    frontier,
                    1,
                    None,
                    None,
                )
            )
        except PublicOperationError:
            # Secondary enrichment only: do not fail a structural status page because the
            # compact projection is lagging, rebuilding, or temporarily unreadable.
            return _readiness_unknown()
    try:
        item = page.items[0] if page.items else None
        if type(item) is not StatusCompactItemModel:
            # Compact omits its singleton when the task title is unreadable. Counting that as
            # zero open obligations would manufacture a clean record out of missing data.
            return _readiness_unknown()
        if item.declared_obligation_count is None or item.open_obligation_count is None:
            return _readiness_unknown()
        declared_obligations = int(item.declared_obligation_count)
        open_obligations = int(item.open_obligation_count)
        unresolved_findings = int(item.unresolved_finding_count)
        has_plan = item.current_plan_event_id is not None
        no_obligations_reason = item.no_obligations_reason
        stale = page.rebuild_state != "current" or bool(page.lag)
        declared_gaps = bool(page.gaps)
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        record_unexpected_exception_without_raising(
            exc,
            component="application.status",
            operation="status_closure_readiness_page_invalid",
            request_id=request_id,
        )
        return _readiness_unknown()
    blocking: list[str] = []
    if open_obligations:
        blocking.append("obligations_open")
    if unresolved_findings:
        blocking.append("findings_unresolved")
    if not has_plan:
        blocking.append("no_plan_published")
    elif declared_obligations == 0 and no_obligations_reason is None:
        blocking.append("no_obligations_declared")
    if stale:
        blocking.append("projection_stale")
    if declared_gaps:
        blocking.append("coverage_gaps_declared")
    return StatusClosureReadinessModel(
        declared_obligation_count=str(declared_obligations),
        no_obligations_reason=no_obligations_reason,
        open_obligation_count=str(open_obligations),
        unresolved_finding_count=str(unresolved_findings),
        blocking_conditions=cast(
            tuple[
                Literal[
                    "obligations_open",
                    "findings_unresolved",
                    "no_plan_published",
                    "no_obligations_declared",
                    "readiness_unknown",
                    "projection_stale",
                    "coverage_gaps_declared",
                ],
                ...,
            ],
            tuple(blocking),
        ),
    )


async def execute_status(
    app: Application,
    request: StatusRequest,
    *,
    route_profile: Literal["policy", "strict"] | None = None,
) -> StatusInternalResult:
    """Return one typed status page without creating a task-ledger consequence."""

    if route_profile not in {None, "policy", "strict"}:
        raise TypeError("status_route_profile_invalid")
    decoded = _decode_cursor(app, request)
    runtime = await app.runtime.route(
        RouteCommand(
            request.session_id,
            request.writer_id,
            RouteAccess.PAYLOAD_READ,
            frozenset({RuntimeCapability.STRUCTURAL_READ, RuntimeCapability.PAYLOAD_READ}),
        )
    )
    try:
        if runtime.session_id != request.session_id or runtime.writer_id != request.writer_id:
            raise _error(PublicErrorCode.SESSION_CONFLICT, "The writer route is inconsistent.")
        if decoded is None:
            frontier, head = await _exact_frontier(
                runtime, None if request.at_frontier is None else int(request.at_frontier)
            )
            expected_version: str | None = None
            position: ProjectionPosition | int | None = None
        else:
            frontier, expected_version, position = decoded
            exact, head = await _exact_frontier(runtime, frontier.sequence)
            if exact != frontier:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.")
            if request.at_frontier is not None and int(request.at_frontier) != frontier.sequence:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.")

        import_status = await _import_status(runtime)
        compact_page: ProjectionPage | None = None
        if request.view == "operation":
            if position is not None:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.")
            if type(request.filter) is not StatusOperationFilterModel:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status filter is invalid.")
            if request.cursor is not None:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.")
            operation = await runtime.ledger.lookup_operation(
                request.writer_id, request.filter.operation_request_id
            )
            continuation = None
            if (
                operation is not None
                and getattr(operation, "state", None) is OperationState.PENDING
                and getattr(operation, "operation_kind", None) is OperationKind.CHECK
            ):
                continuation = await _operation_continuation(
                    app,
                    request.writer_id,
                    operation,
                )
            try:
                page = _operation_page_from_record(
                    request.filter.operation_request_id, operation, continuation
                )
            except (AttributeError, TypeError, ValueError) as exc:
                raise _error(
                    PublicErrorCode.STORAGE_CORRUPT, "The stored operation result is invalid."
                ) from exc
            # Operation recovery is structural: the operation page is authoritative. Compact
            # projection only enriches coverage/closure/frontiers and must not fail recovery
            # when lagging, rebuilding, or missing.
            #
            # Totality: capture frontier/head-derived enrichment first, then optionally replace
            # from a successful compact page. Every path that reaches StatusInternalResult has
            # head, effective, lag, projection_version, and rebuild_state bound — the shape of
            # the run-4 AttributeError was an unbound/stale local on a recovery path.
            structural_head = head
            structural_effective = frontier
            structural_lag = structural_head.sequence - frontier.sequence
            structural_version = runtime.projection_version
            compact_page = None
            coverage = _unknown_structural_coverage()
            gaps: tuple[str, ...] = ()
            head = structural_head
            effective = structural_effective
            lag = structural_lag
            projection_version = structural_version
            rebuild_state: Literal["current", "rebuild_required", "rebuilding"] = "rebuild_required"
            try:
                raw_page = await runtime.ledger.query_projection(
                    ProjectionQuery(
                        runtime.session_id,
                        ProjectionView.COMPACT.value,
                        None,
                        frontier,
                        1,
                        None,
                        expected_version,
                    )
                )
            except PublicOperationError:
                pass
            else:
                try:
                    next_coverage = raw_page.coverage
                    next_gaps = raw_page.gaps
                    next_head = raw_page.head_frontier
                    next_effective = raw_page.effective_frontier
                    next_lag = raw_page.lag
                    next_version = raw_page.projection_version
                    next_rebuild = raw_page.rebuild_state
                except AttributeError as exc:
                    # Unexpected page shape. Recovery still succeeds on the structural defaults
                    # already bound above — the operation page is authoritative and enrichment is
                    # secondary — but degrading silently would discard the only signal that the
                    # compact projection is returning something it should not. Record the bounded
                    # reason so the next occurrence is diagnosable instead of invisible.
                    record_unexpected_exception_without_raising(
                        exc,
                        component="application.status",
                        operation="status_operation_compact_enrichment_invalid",
                        request_id=request.request_id,
                    )
                else:
                    compact_page = raw_page
                    coverage = next_coverage
                    gaps = next_gaps
                    head = next_head
                    effective = next_effective
                    lag = next_lag
                    projection_version = next_version
                    rebuild_state = next_rebuild
        elif request.view == "advice":
            if position is not None:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.")
            raw_page = await runtime.ledger.query_projection(
                ProjectionQuery(
                    runtime.session_id,
                    ProjectionView.COMPACT.value,
                    None,
                    frontier,
                    1,
                    None,
                    expected_version,
                )
            )
            # The advice view derives from the same compact page closure readiness needs.
            compact_page = raw_page
            snapshot: AdviceSnapshot | None = None
            if runtime.observation is not None:
                workspace = None
                lookup = getattr(runtime.observation, "workspace_for_yoetz_session", None)
                if callable(lookup):
                    workspace = lookup(runtime.session_id)
                if type(workspace) is str:
                    session_load = getattr(
                        runtime.observation, "load_advice_snapshot_for_session", None
                    )
                    if callable(session_load):
                        loaded = session_load(
                            workspace=workspace, yoetz_session_id=runtime.session_id
                        )
                        snapshot = loaded if isinstance(loaded, AdviceSnapshot) else None
                    else:
                        loaded = runtime.observation.load_advice_snapshot(workspace)
                        snapshot = loaded if isinstance(loaded, AdviceSnapshot) else None
            items: tuple[dict[str, JsonValue], ...] = ()
            if snapshot is not None:
                advice = snapshot
                verification_state = (
                    "stale"
                    if "verification_stale" in advice.confidence_coverage.known_gaps
                    else (
                        "unavailable"
                        if "policy_untrusted" in advice.confidence_coverage.known_gaps
                        else "current"
                    )
                )
                items = tuple(
                    {
                        "finding_id": str(item.finding_id),
                        "rule_code": item.rule_code,
                        "priority": item.priority,
                        "evidence_commitments": tuple(
                            sorted(
                                {
                                    advice.evidence_basis_digest,
                                    *(
                                        ref
                                        for ref in item.evidence_refs
                                        if ref.startswith(("sha256:", "hmac-sha256:"))
                                    ),
                                },
                                key=str.encode,
                            )
                        ),
                        "coverage": coverage_to_json(item.coverage),
                        "freshness_frontier": item.freshness_frontier,
                        "verification_state": verification_state,
                        "semantic_state": (
                            "ready" if item.origin == "semantic_model_derived" else "disabled"
                        ),
                        "recommended_next_action": item.recommended_next_action,
                    }
                    for item in advice.ranked_items[: int(request.limit)]
                )
            page = StatusAdvicePageModel.model_validate(
                {
                    "projection_format": "yoetz.advice-snapshot/1",
                    "items": items,
                    "next_cursor": None,
                }
            )
            coverage = snapshot.confidence_coverage if snapshot is not None else raw_page.coverage
            gaps = (
                snapshot.confidence_coverage.known_gaps if snapshot is not None else raw_page.gaps
            )
            head = raw_page.head_frontier
            effective = raw_page.effective_frontier
            lag = raw_page.lag
            projection_version = raw_page.projection_version
            rebuild_state = raw_page.rebuild_state
        elif request.view == "candidate_findings":
            if position is not None and type(position) is not int:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.")
            page, coverage, gaps, _ = await _candidate_page(
                app, request, runtime, frontier, head, position or 0
            )
            effective = frontier
            lag = head.sequence - frontier.sequence
            projection_version = runtime.projection_version
            rebuild_state = "current"
        else:
            if type(position) is int:
                raise _error(PublicErrorCode.INVALID_REQUEST, "The status cursor is invalid.")
            query = ProjectionQuery(
                runtime.session_id,
                request.view,
                _port_filter(request.filter),
                frontier,
                int(request.limit),
                cast(ProjectionPosition | None, position),
                expected_version,
            )
            raw_page = await runtime.ledger.query_projection(query)
            if request.view == "compact":
                compact_page = raw_page
            next_cursor = (
                None
                if raw_page.next_position is None
                else _encode_cursor(
                    app,
                    request,
                    frontier,
                    raw_page.projection_version,
                    raw_page.next_position,
                )
            )
            page = _page_model(raw_page, next_cursor)
            if route_profile is not None and type(page) is StatusVersionsPageModel:
                page = StatusVersionsPageModel(
                    items=tuple(
                        StatusVersionSliceModel.model_validate(
                            {
                                **item.model_dump(mode="json", exclude_none=True),
                                "route_profile": route_profile,
                            }
                        )
                        for item in page.items
                    ),
                    next_cursor=None,
                )
            coverage = raw_page.coverage
            gaps = raw_page.gaps
            head = raw_page.head_frontier
            effective = raw_page.effective_frontier
            lag = raw_page.lag
            projection_version = raw_page.projection_version
            rebuild_state = raw_page.rebuild_state
        closure_readiness = await _closure_readiness(
            runtime, frontier, compact_page, request.request_id
        )
        return StatusInternalResult(
            "0.1",
            "1.0.0",
            request.request_id,
            True,
            runtime.task_id,
            runtime.session_id,
            cast(str, runtime.writer_id),
            request.view,
            frontier,
            head,
            effective,
            effective,
            lag,
            projection_version,
            rebuild_state,
            page,
            coverage,
            gaps,
            import_status,
            closure_readiness,
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PublicOperationError):
            raise
        raise _error(PublicErrorCode.INVALID_REQUEST, "The status request is invalid.") from exc
    finally:
        await app.runtime.release(runtime)
