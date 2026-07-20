"""Record an attributable response to one immutable finding version."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from yoetz.application.unit_of_work import PreparedMutation, run_prepared_append
from yoetz.domain.events import (
    EventDraft,
    EventSchema,
    LedgerRecord,
    ResponseRecordedPayload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.findings import ResponseDisposition, WaiverScope
from yoetz.domain.values import (
    Actor,
    ActorType,
    Frontier,
    actor_id,
    event_id,
    evidence_id,
    finding_id,
    frontier_from_json,
    result_id,
    timestamp_from_datetime,
    timestamp_from_string,
)
from yoetz.kernel.reducers import replay
from yoetz.ports.clock import ClockPort
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    AcceptedEventSummary,
    AppendCommand,
    AppendEntry,
    AppendResult,
    AppendWarning,
    OperationKind,
    OperationState,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource
from yoetz.ports.runtime import BundleRuntimePort, RouteAccess, RouteCommand, TaskRuntime
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_encode,
    request_digest,
    strict_json_parse,
)
from yoetz.protocol.coverage import (
    Coverage,
    PublicationChannel,
    coverage_for_channel,
    coverage_to_json,
)
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import (
    RespondAcceptedEventModel,
    RespondEvidenceSummaryModel,
    RespondRequest,
    RespondResponseModel,
)

__all__ = ["Application", "RespondInternalResult", "execute_respond"]

_POLICY_PACKS = ("research-evidence/0.1.0", "work-integrity/0.1.0")


class Application(Protocol):
    runtime: BundleRuntimePort
    clock: ClockPort
    ids: IdPort
    waiver_policy_digest: str

    def authorizes_waiver(self, request: RespondRequest) -> bool: ...


@dataclass(frozen=True, slots=True)
class RespondInternalResult:
    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: str
    ok: Literal[True]
    task_id: str
    session_id: str
    writer_id: str
    subject_frontier: Frontier
    result_frontier: Frontier
    accepted_event: RespondAcceptedEventModel
    response: RespondResponseModel
    coverage: Coverage
    warning_codes: tuple[Literal["waiver_expired_at_recording"], ...]
    engine_version: str
    projection_version: str

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "protocol_version": self.protocol_version,
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "ok": self.ok,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "writer_id": self.writer_id,
            "subject_frontier": dict(self.subject_frontier.as_wire().items()),
            "result_frontier": dict(self.result_frontier.as_wire().items()),
            "accepted_event": cast(JsonValue, self.accepted_event.model_dump(mode="json")),
            # ``RespondResponseModel.optional_non_null_fields`` (reason/waiver_scope/waiver_expiry)
            # must be entirely omitted from the wire when absent, never present as an explicit
            # null; ``_ClosedModel`` rejects a reflexive re-validation otherwise (for example
            # when this internal JSON is later projected for an ordinary client).
            "response": cast(JsonValue, self.response.model_dump(mode="json", exclude_none=True)),
            "coverage": coverage_to_json(self.coverage),
            "warning_codes": self.warning_codes,
            "versions": {
                "protocol_version": "0.1",
                "engine_version": self.engine_version,
                "projection_version": self.projection_version,
                "policy_packs": _POLICY_PACKS,
            },
        }


def _error(
    code: PublicErrorCode,
    message: str,
    *,
    retryable: bool = False,
) -> PublicOperationError:
    return PublicOperationError(code, message, retryable)


def _mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ProtocolValueError("stored_result_shape_invalid")
    source = cast(Mapping[object, object], value)
    if any(type(key) is not str for key in source):
        raise ProtocolValueError("stored_result_shape_invalid")
    return cast(Mapping[str, JsonValue], source)


def _decode_append_result(data: bytes) -> AppendResult:
    try:
        source = _mapping(strict_json_parse(data))
        if frozenset(source) != frozenset(
            {"accepted", "subject_frontier", "result_frontier", "warnings"}
        ):
            raise ProtocolValueError("stored_result_shape_invalid")
        accepted_raw = source["accepted"]
        warnings_raw = source["warnings"]
        if type(accepted_raw) not in {tuple, list} or type(warnings_raw) not in {tuple, list}:
            raise ProtocolValueError("stored_result_shape_invalid")
        accepted = tuple(
            AcceptedEventSummary(
                cast(str, item["event_id"]),
                int(cast(str, item["ingestion_sequence"])),
                int(cast(str, item["writer_sequence"])),
                cast(str, item["entry_digest"]),
                cast(Literal["projected", "unknown_unprojected"], item["projection_status"]),
            )
            for item in (
                _mapping(value)
                for value in cast(tuple[JsonValue, ...] | list[JsonValue], accepted_raw)
            )
        )
        warnings = tuple(
            sorted(
                (
                    AppendWarning(cast(str, value))
                    for value in cast(tuple[JsonValue, ...] | list[JsonValue], warnings_raw)
                ),
                key=lambda item: item.value.encode("ascii"),
            )
        )
        return AppendResult(
            "replayed",
            accepted,
            frontier_from_json(source["subject_frontier"]),
            frontier_from_json(source["result_frontier"]),
            warnings,
        )
    except (TypeError, ValueError) as exc:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The stored response is invalid.") from exc


def _channel(request: RespondRequest) -> PublicationChannel:
    try:
        return PublicationChannel(request.client.integration.value)
    except ValueError as exc:
        raise _error(PublicErrorCode.INVALID_REQUEST, "The response channel is invalid.") from exc


def _identity(request: RespondRequest, *, policy_digest: str) -> JsonValue:
    return cast(
        JsonValue,
        {
            "protocol_version": request.protocol_version,
            "schema_version": request.schema_version,
            "request_id": request.request_id,
            "session_id": request.session_id,
            "writer_id": request.writer_id,
            "expected_frontier": request.expected_frontier.model_dump(mode="json"),
            "finding_id": request.finding_id,
            "finding_frontier": request.finding_frontier.model_dump(mode="json"),
            "disposition": request.disposition,
            "reason": request.reason,
            "waiver_scope": request.waiver_scope,
            "waiver_expiry": request.waiver_expiry,
            "evidence_refs": () if request.evidence_refs is None else request.evidence_refs,
            "actor": {
                "actor_id": request.actor.actor_id,
                "actor_type": request.actor.actor_type.value,
            },
            "client": {
                "integration": request.client.integration.value,
                "kind": request.client.kind.value,
                "version": request.client.version,
            },
            "waiver_policy_digest": policy_digest,
        },
    )


async def _preflight(
    runtime: TaskRuntime, request: RespondRequest, digest: str
) -> AppendResult | None:
    operation = await runtime.ledger.lookup_operation(request.writer_id, request.request_id)
    if operation is None:
        return None
    if operation.request_digest != digest or operation.operation_kind is not OperationKind.RESPOND:
        raise _error(PublicErrorCode.IDEMPOTENCY_CONFLICT, "The request ID was already used.")
    if operation.state is OperationState.COMPLETE:
        assert operation.result_canonical is not None
        return _decode_append_result(operation.result_canonical)
    if operation.state is OperationState.QUARANTINED:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The response operation is quarantined.")
    raise _error(
        PublicErrorCode.OPERATION_PENDING,
        "The response operation is pending.",
        retryable=True,
    )


async def _records_through(runtime: TaskRuntime, frontier: Frontier) -> tuple[LedgerRecord, ...]:
    records = tuple(
        [
            record
            async for record in runtime.ledger.load_events(
                runtime.session_id, through=frontier.sequence
            )
        ]
    )
    projection = replay(records)
    if Frontier(projection.frontier, projection.head_digest) != frontier:
        raise _error(PublicErrorCode.FRONTIER_CONFLICT, "The response frontier is not current.")
    return records


async def _accepted_record(runtime: TaskRuntime, result: AppendResult) -> LedgerRecord:
    if len(result.accepted) != 1:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The response event range is invalid.")
    summary = result.accepted[0]
    records = tuple(
        [
            record
            async for record in runtime.ledger.load_events(
                runtime.session_id,
                after=summary.ingestion_sequence - 1,
                through=summary.ingestion_sequence,
            )
        ]
    )
    if len(records) != 1:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The response event range is invalid.")
    record = records[0]
    if (
        str(record.event_id) != summary.event_id
        or record.writer.sequence != summary.writer_sequence
        or record.entry_digest != summary.entry_digest
        or record.schema.name != "response_recorded"
        or type(record.payload) is not ResponseRecordedPayload
    ):
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The response event range is invalid.")
    return record


async def _public_result(
    request: RespondRequest,
    runtime: TaskRuntime,
    result: AppendResult,
) -> RespondInternalResult:
    record = await _accepted_record(runtime, result)
    payload = cast(ResponseRecordedPayload, record.payload)
    evidence = tuple(RespondEvidenceSummaryModel(reference_id=ref) for ref in payload.evidence_refs)
    response_wire: dict[str, object] = {
        "response_event_id": record.event_id,
        "finding_id": payload.finding_id,
        "finding_frontier": dict(payload.finding_frontier.as_wire().items()),
        "disposition": payload.disposition.value,
        "evidence": evidence,
    }
    if payload.reason is not None:
        response_wire["reason"] = payload.reason
    if payload.waiver_scope is not None:
        response_wire["waiver_scope"] = payload.waiver_scope.value
    if payload.waiver_expiry is not None:
        response_wire["waiver_expiry"] = payload.waiver_expiry.wire
    response = RespondResponseModel.model_validate(response_wire)
    warning_codes: tuple[Literal["waiver_expired_at_recording"], ...] = ()
    if (
        payload.waiver_expiry is not None
        and payload.waiver_expiry.wire < record.ledger.accepted_at.wire
    ):
        warning_codes = ("waiver_expired_at_recording",)
    return RespondInternalResult(
        "0.1",
        "1.0.0",
        request.request_id,
        True,
        runtime.task_id,
        runtime.session_id,
        cast(str, runtime.writer_id),
        result.subject_frontier,
        result.result_frontier,
        RespondAcceptedEventModel(
            event_id=record.event_id,
            writer_sequence=str(record.writer.sequence),
            ingestion_sequence=str(record.ledger.ingestion_sequence),
            accepted_at=record.ledger.accepted_at.wire,
            entry_digest=record.entry_digest,
        ),
        response,
        record.coverage,
        warning_codes,
        runtime.engine_version,
        runtime.projection_version,
    )


async def execute_respond(app: Application, request: RespondRequest) -> RespondInternalResult:
    """Validate, object-publish, and atomically append one ``response_recorded`` event."""

    runtime = await app.runtime.route(
        RouteCommand(
            request.session_id,
            request.writer_id,
            RouteAccess.WRITE,
            frozenset({RuntimeCapability.WRITE, RuntimeCapability.PAYLOAD_READ}),
        )
    )
    try:
        if runtime.session_id != request.session_id or runtime.writer_id != request.writer_id:
            raise _error(PublicErrorCode.SESSION_CONFLICT, "The writer route is inconsistent.")
        if request.disposition == "waived" and (
            request.client.integration.value != "local_cli"
            or request.actor.actor_type.value != "human"
            or not app.authorizes_waiver(request)
        ):
            raise _error(PublicErrorCode.INVALID_REQUEST, "The waiver is not authorized.")
        digest = request_digest(_identity(request, policy_digest=app.waiver_policy_digest))
        result = await _preflight(runtime, request, digest)
        if result is None:
            finding_frontier = Frontier(
                int(request.finding_frontier.sequence), request.finding_frontier.head_digest
            )
            finding_records = await _records_through(runtime, finding_frontier)
            finding_projection = replay(finding_records)
            finding_record = finding_projection.findings.get(finding_id(request.finding_id))
            if finding_record is None or finding_record.payload is None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "The finding is unavailable at the supplied frontier.",
                )
            current = Frontier(
                int(request.expected_frontier.sequence), request.expected_frontier.head_digest
            )
            current_records = await _records_through(runtime, current)
            current_projection = replay(current_records)
            for ref in () if request.evidence_refs is None else request.evidence_refs:
                present = (
                    current_projection.evidence.get(evidence_id(ref))
                    if ref.startswith("evd_")
                    else current_projection.results.get(result_id(ref))
                )
                if present is None or present.payload is None:
                    raise _error(
                        PublicErrorCode.INVALID_REQUEST, "A response reference is invalid."
                    )
            coverage = coverage_for_channel(_channel(request))
            payload = ResponseRecordedPayload(
                finding_id(request.finding_id),
                finding_frontier,
                ResponseDisposition(request.disposition),
                request.reason,
                None if request.waiver_scope is None else WaiverScope(request.waiver_scope),
                None
                if request.waiver_expiry is None
                else timestamp_from_string(request.waiver_expiry),
                tuple(
                    evidence_id(ref) if ref.startswith("evd_") else result_id(ref)
                    for ref in (() if request.evidence_refs is None else request.evidence_refs)
                ),
            )
            now = app.clock.now_utc()
            draft = EventDraft(
                event_id(app.ids.new(IdKind.EVENT)),
                EventSchema("response_recorded", "1.0.0"),
                timestamp_from_datetime(now),
                (finding_record.source_event_id,),
                payload,
                (),
                payload.evidence_refs,
            )
            payload_bytes = canonical_encode(encode_payload(payload))
            metadata = ObjectMetadata(
                ObjectKind.EVENT_PAYLOAD,
                media_type_for("response_recorded"),
                runtime.task_id,
                now,
            )
            staged = await runtime.objects.stage(
                ObjectSource(data=payload_bytes, declared_size=len(payload_bytes)), metadata
            )
            ref = await runtime.objects.finalize(staged)
            author = Actor(
                actor_id(request.actor.actor_id),
                ActorType(request.actor.actor_type.value),
                coverage.authorship_assurance,
            )
            command = AppendCommand(
                runtime.task_id,
                runtime.session_id,
                cast(str, runtime.writer_id),
                request.request_id,
                OperationKind.RESPOND,
                digest,
                current.sequence,
                (
                    AppendEntry(
                        draft,
                        author,
                        ref,
                        ref.commitment,
                        metadata.media_type,
                        ref.plaintext_size,
                        _channel(request),
                        coverage,
                        "projected",
                    ),
                ),
            )
            result = await run_prepared_append(
                runtime.ledger,
                PreparedMutation(
                    cast(str, runtime.writer_id),
                    request.request_id,
                    digest,
                    current.sequence,
                    (ref,),
                    command,
                ),
            )
        return await _public_result(request, runtime, result)
    except ProtocolValueError as exc:
        raise _error(PublicErrorCode.INVALID_REQUEST, "The response request is invalid.") from exc
    finally:
        await app.runtime.release(runtime)
