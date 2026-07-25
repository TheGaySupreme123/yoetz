"""Bounded read-only status projections at an authenticated stable frontier."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

from yoetz.application.check import CheckScope, run_deterministic_policies
from yoetz.domain.findings import FINDING_KIND_TRAITS, FindingOrigin
from yoetz.domain.observation import AdviceSnapshot
from yoetz.domain.values import Frontier
from yoetz.kernel.deterministic_checks import (
    DeterministicAssessment,
    build_deterministic_case,
    finding_basis_to_status_json,
)
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ledger import (
    AssignmentProjectionFilter,
    EvidenceProjectionFilter,
    FindingProjectionPosition,
    FindingsProjectionFilter,
    HistoryProjectionFilter,
    HistoryProjectionPosition,
    IdProjectionPosition,
    ObligationsProjectionFilter,
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
    CheckType,
    Coverage,
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
    StatusPage,
    StatusRequest,
    StatusVersionsPageModel,
)

__all__ = ["Application", "StatusInternalResult", "execute_status"]

_PACKS = ("research-evidence/0.1.0", "work-integrity/0.1.0")
_CURSOR_VERSION = "1"


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

    def as_json(self) -> dict[str, JsonValue]:
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
            "page": cast(JsonValue, self.page.model_dump(mode="json", exclude_none=False)),
            "coverage": coverage_to_json(self.coverage),
            "gaps": self.gaps,
            "import_status": cast(
                JsonValue, self.import_status.model_dump(mode="json", exclude_none=False)
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
    raise ValueError("status_filter_invalid")


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
        raise _error(
            PublicErrorCode.FRONTIER_CONFLICT,
            (
                "The requested frontier is unavailable. Call status to read the current frontier, "
                "then retry idempotently with the same request_id."
            ),
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

    projection = replay(records)
    if Frontier(projection.frontier, projection.head_digest) != frontier:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The status frontier is inconsistent.")
    availability = await runtime.ledger.load_case_availability(
        runtime.session_id, frontier, projection
    )
    case = build_deterministic_case(projection, records, availability)
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


async def execute_status(app: Application, request: StatusRequest) -> StatusInternalResult:
    """Return one typed status page without creating a task-ledger consequence."""

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
        if request.view == "advice":
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
            coverage = raw_page.coverage
            gaps = raw_page.gaps
            head = raw_page.head_frontier
            effective = raw_page.effective_frontier
            lag = raw_page.lag
            projection_version = raw_page.projection_version
            rebuild_state = raw_page.rebuild_state
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
        )
    except (TypeError, ValueError) as exc:
        if isinstance(exc, PublicOperationError):
            raise
        raise _error(PublicErrorCode.INVALID_REQUEST, "The status request is invalid.") from exc
    finally:
        await app.runtime.release(runtime)
