"""Prepare and atomically publish one strict batch of work events."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Final, Literal, Protocol, cast

from yoetz.application.unit_of_work import PreparedMutation, run_prepared_append
from yoetz.domain.events import (
    PAYLOAD_TYPES,
    EventDraft,
    EventPayload,
    EventSchema,
    LedgerRecord,
    UnknownEvent,
    decode_payload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.values import (
    Actor,
    ActorType,
    EventId,
    Frontier,
    actor_id,
    event_id,
    evidence_id,
    freeze_json,
    frontier_from_json,
    object_id,
    result_id,
    timestamp_from_string,
)
from yoetz.domain.values import (
    JsonValue as DomainJsonValue,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.ledger import (
    AcceptedEventSummary,
    AppendCommand,
    AppendEntry,
    AppendResult,
    AppendWarning,
    OperationKind,
    OperationState,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.runtime import (
    BundleRuntimePort,
    RouteAccess,
    RouteCommand,
    TaskRuntime,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_encode,
    request_digest,
    strict_json_parse,
)
from yoetz.protocol.coverage import (
    AuthorshipAssurance,
    Coverage,
    LedgerFreshness,
    PublicationChannel,
    coverage_for_channel,
    coverage_to_json,
)
from yoetz.protocol.errors import (
    PROTOCOL_REASON_CODES,
    ProtocolValueError,
    PublicErrorCode,
    PublicOperationError,
)
from yoetz.protocol.models import (
    MAX_CANONICAL_REQUEST_BYTES,
    MAX_EVENTS_PER_BATCH,
    PublishWorkAcceptedEventModel,
    PublishWorkRequestModel,
    PublishWorkVersionSliceModel,
)

__all__ = [
    "Application",
    "PreparedPublication",
    "PublishWorkInternalResult",
    "execute_publish_work",
    "prepare_publication",
]

_ORDINARY_FAMILIES = frozenset(
    {
        "plan_published",
        "obligation_published",
        "assignment_recorded",
        "decision_recorded",
        "action_recorded",
        "result_recorded",
        "evidence_recorded",
        "claim_recorded",
        "plan_revised",
    }
)
_IMPORT_FAMILIES = frozenset(
    {
        "action_recorded",
        "result_recorded",
        "evidence_recorded",
    }
)
_STATE_SENSITIVE_FAMILIES = frozenset(
    {
        "plan_published",
        "obligation_published",
        "assignment_recorded",
        "decision_recorded",
        "result_recorded",
        "claim_recorded",
        "plan_revised",
    }
)
_UNKNOWN_GAP = "unknown_event_schema_preserved"
_POLICY_PACKS = ("research-evidence/0.1.0", "work-integrity/0.1.0")


class Application(Protocol):
    """Least surface required from the ready application composition."""

    runtime: BundleRuntimePort
    clock: ClockPort

    def authorizes_import_publication(self, request: PublishWorkRequestModel) -> bool:
        """Return a trusted service fact, never a caller assertion."""
        ...


@dataclass(frozen=True, slots=True)
class _PreparedDraft:
    draft: EventDraft
    payload_bytes: bytes
    projection_status: Literal["projected", "unknown_unprojected"]


@dataclass(frozen=True, slots=True)
class PreparedPublication:
    """Validated batch precursor; it owns no random or durable object identity."""

    channel: PublicationChannel
    author: Actor
    coverage: Coverage
    drafts: tuple[_PreparedDraft, ...]


@dataclass(frozen=True, slots=True)
class PublishWorkInternalResult:
    """Closed structural publication success before client-specific projection."""

    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: str
    request_digest: str
    ok: Literal[True]
    outcome: Literal["accepted", "replayed"]
    task_id: str
    session_id: str
    writer_id: str
    subject_frontier: Frontier
    result_frontier: Frontier
    accepted_events: tuple[PublishWorkAcceptedEventModel, ...]
    warning_codes: tuple[str, ...]
    coverage: Coverage
    gaps: tuple[str, ...]
    versions: PublishWorkVersionSliceModel

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "protocol_version": self.protocol_version,
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "ok": self.ok,
            "outcome": self.outcome,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "writer_id": self.writer_id,
            "subject_frontier": dict(self.subject_frontier.as_wire().items()),
            "result_frontier": dict(self.result_frontier.as_wire().items()),
            "accepted_events": tuple(
                cast(JsonValue, event.model_dump(mode="json", exclude_none=False))
                for event in self.accepted_events
            ),
            "warning_codes": self.warning_codes,
            "coverage": coverage_to_json(self.coverage),
            "gaps": self.gaps,
            "versions": cast(JsonValue, self.versions.model_dump(mode="json")),
        }


def _error(
    code: PublicErrorCode,
    message: str,
    *,
    retryable: bool = False,
    reason_code: str | None = None,
) -> PublicOperationError:
    details = None if reason_code is None else {"reason_code": reason_code}
    return PublicOperationError(code, message, retryable, safe_details=details)


# The event-draft envelope fields, which are frozen schema names rather than caller-chosen keys.
# Only these may appear after the draft ordinal in a public error location.
_LOCATABLE_DRAFT_SUBFIELDS: Final = frozenset(
    {
        "artifact_refs",
        "causal_parents",
        "event_id",
        "evidence_refs",
        "occurred_at",
        "payload",
        "schema",
    }
)


def _draft_pointer(event_index: int, subfield: str | None) -> str | None:
    """Locate one rejected draft without ever naming caller-supplied keys or values.

    Every segment is a frozen schema name plus the draft's ordinal, which is bounded by
    ``MAX_EVENTS_PER_BATCH``. Nothing here is derived from the submitted payload.
    """

    if type(event_index) is not int or not 0 <= event_index < MAX_EVENTS_PER_BATCH:
        return None
    if subfield is None:
        return f"/event_drafts/{event_index}"
    if subfield not in _LOCATABLE_DRAFT_SUBFIELDS:
        return None
    return f"/event_drafts/{event_index}/{subfield}"


def _event_invalid(
    reason_code: str = "invalid_event_value_type",
    *,
    event_index: int | None = None,
    subfield: str | None = None,
) -> PublicOperationError:
    # Only a stale frontier is fixed by rereading status; every other reason needs the event
    # payload corrected first, and retrying it unchanged would fail the same way.
    if reason_code == "frontier_changed":
        message = (
            "The event batch is invalid. Call status to read the current frontier, then retry "
            "idempotently with the same request_id."
        )
    else:
        message = "The event batch is invalid. Correct the event payload before retrying."
    details: dict[str, str] = {"reason_code": reason_code}
    if event_index is not None:
        pointer = _draft_pointer(event_index, subfield)
        if pointer is not None:
            # Which draft failed is the difference between a one-line fix and re-deriving the
            # whole batch; a batch may carry up to MAX_EVENTS_PER_BATCH drafts.
            details["field"] = pointer
    return PublicOperationError(PublicErrorCode.EVENT_INVALID, message, False, safe_details=details)


def _mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ProtocolValueError("invalid_event_value_type")
    source = cast(Mapping[object, object], value)
    if any(type(key) is not str for key in source):
        raise ProtocolValueError("invalid_event_value_type")
    return cast(Mapping[str, JsonValue], source)


def _field(source: Mapping[str, JsonValue], key: str) -> JsonValue:
    try:
        return source[key]
    except Exception as exc:
        raise ProtocolValueError("missing_payload_field") from exc


def _tuple_field(source: Mapping[str, JsonValue], key: str) -> tuple[JsonValue, ...]:
    value = _field(source, key)
    if type(value) is not tuple and type(value) is not list:
        raise ProtocolValueError("invalid_event_value_type")
    return tuple(cast(tuple[JsonValue, ...] | list[JsonValue], value))


def _channel_for(request: PublishWorkRequestModel) -> PublicationChannel:
    try:
        return PublicationChannel(request.client.integration.value)
    except ValueError as exc:
        raise _event_invalid("event_family_not_admitted") from exc


def _coverage_for(channel: PublicationChannel, *, has_unknown: bool) -> Coverage:
    baseline = coverage_for_channel(channel)
    if channel is PublicationChannel.CODEX_JSONL_IMPORT:
        baseline = replace(
            baseline,
            authorship_assurance=AuthorshipAssurance.HARNESS_OBSERVED,
        )
    if not has_unknown:
        return baseline
    return replace(
        baseline,
        ledger_freshness=LedgerFreshness.PARTIAL,
        known_gaps=tuple(sorted((*baseline.known_gaps, _UNKNOWN_GAP), key=str.encode)),
    )


def _reason_code_of(exc: BaseException, fallback: str = "invalid_event_value_type") -> str:
    reason = exc.args[0] if exc.args and type(exc.args[0]) is str else fallback
    return reason if reason in PROTOCOL_REASON_CODES else fallback


def _decode_draft(value: JsonValue, event_index: int) -> _PreparedDraft:
    """Decode one draft, attributing any rejection to that draft and its owning field."""

    source = _mapping(value)

    def _locate[T](subfield: str, decode: Callable[[], T]) -> T:
        try:
            return decode()
        except PublicOperationError:
            raise
        except (TypeError, ValueError) as exc:
            raise _event_invalid(
                _reason_code_of(exc), event_index=event_index, subfield=subfield
            ) from exc

    schema_source = _locate("schema", lambda: _mapping(_field(source, "schema")))
    schema = _locate(
        "schema",
        lambda: EventSchema(
            cast(str, _field(schema_source, "name")),
            cast(str, _field(schema_source, "version")),
        ),
    )
    raw_payload = _locate("payload", lambda: freeze_json(_field(source, "payload")))
    if schema in PAYLOAD_TYPES:
        payload: EventPayload | DomainJsonValue = _locate(
            "payload", lambda: decode_payload(schema, raw_payload)
        )
        payload_json = _locate("payload", lambda: encode_payload(payload))
        projection_status = "projected"
    else:
        payload = raw_payload
        payload_json = cast(JsonValue, raw_payload)
        projection_status = "unknown_unprojected"
    draft = EventDraft(
        _locate("event_id", lambda: event_id(_field(source, "event_id"))),
        schema,
        _locate("occurred_at", lambda: timestamp_from_string(_field(source, "occurred_at"))),
        _locate(
            "causal_parents",
            lambda: tuple(event_id(item) for item in _tuple_field(source, "causal_parents")),
        ),
        payload,
        _locate(
            "artifact_refs",
            lambda: tuple(object_id(item) for item in _tuple_field(source, "artifact_refs")),
        ),
        _locate(
            "evidence_refs",
            lambda: tuple(
                evidence_id(item)
                if type(item) is str and item.startswith("evd_")
                else result_id(item)
                for item in _tuple_field(source, "evidence_refs")
            ),
        ),
    )
    return _PreparedDraft(draft, canonical_encode(payload_json), projection_status)


def _validate_admission(
    app: Application,
    request: PublishWorkRequestModel,
    channel: PublicationChannel,
    drafts: tuple[_PreparedDraft, ...],
) -> None:
    trusted_import = channel is PublicationChannel.CODEX_JSONL_IMPORT
    if trusted_import and not app.authorizes_import_publication(request):
        raise _event_invalid("event_family_not_admitted")
    admitted = _IMPORT_FAMILIES if trusted_import else _ORDINARY_FAMILIES
    for index, item in enumerate(drafts):
        known = item.draft.schema in PAYLOAD_TYPES
        if (known and item.draft.schema.name not in admitted) or (not known and not trusted_import):
            raise _event_invalid("event_family_not_admitted", event_index=index, subfield="schema")
    if request.expected_frontier is None and any(
        item.draft.schema in PAYLOAD_TYPES and item.draft.schema.name in _STATE_SENSITIVE_FAMILIES
        for item in drafts
    ):
        raise _event_invalid("frontier_changed")


def _validate_order(drafts: tuple[_PreparedDraft, ...]) -> None:
    event_ids = tuple(item.draft.event_id for item in drafts)
    if len(event_ids) != len(set(event_ids)):
        seen: set[EventId] = set()
        duplicate_at = 0
        for index, value in enumerate(event_ids):
            if value in seen:
                duplicate_at = index
                break
            seen.add(value)
        raise _event_invalid("duplicate_set_member", event_index=duplicate_at, subfield="event_id")
    position = {event_id: index for index, event_id in enumerate(event_ids)}
    for index, item in enumerate(drafts):
        if any(position.get(parent, -1) >= index for parent in item.draft.causal_parents):
            raise _event_invalid(
                "invalid_event_value_type", event_index=index, subfield="causal_parents"
            )


def prepare_publication(
    request: PublishWorkRequestModel,
    *,
    channel: PublicationChannel,
    app: Application,
) -> PreparedPublication:
    """Decode, normalize, authorize, and cross-check one bounded batch."""

    if not 1 <= len(request.event_drafts) <= MAX_EVENTS_PER_BATCH:
        raise _error(PublicErrorCode.INVALID_REQUEST, "The event batch size is invalid.")
    decoded: list[_PreparedDraft] = []
    for index, value in enumerate(request.event_drafts):
        try:
            decoded.append(_decode_draft(value, index))
        except PublicOperationError:
            raise
        except (TypeError, ValueError) as exc:
            # Anything not already attributed to a field still names its draft, so the caller
            # never has to re-derive which member of the batch was rejected.
            raise _event_invalid(_reason_code_of(exc), event_index=index) from exc
    drafts = tuple(decoded)
    try:
        _validate_order(drafts)
        _validate_admission(app, request, channel, drafts)
    except PublicOperationError:
        raise
    except (TypeError, ValueError) as exc:
        raise _event_invalid(_reason_code_of(exc)) from exc

    has_unknown = any(item.projection_status == "unknown_unprojected" for item in drafts)
    coverage = _coverage_for(channel, has_unknown=has_unknown)
    author = Actor(
        actor_id(request.actor.actor_id),
        ActorType(request.actor.actor_type.value),
        coverage.authorship_assurance,
    )
    return PreparedPublication(channel, author, coverage, drafts)


def _request_identity(
    request: PublishWorkRequestModel,
    prepared: PreparedPublication,
    commitments: tuple[str, ...],
) -> JsonValue:
    expected = (
        None
        if request.expected_frontier is None
        else request.expected_frontier.model_dump(mode="json")
    )
    return {
        "protocol": "yoetz",
        "protocol_version": request.protocol_version,
        "schema_version": request.schema_version,
        "request_id": request.request_id,
        "session_id": request.session_id,
        "writer_id": request.writer_id,
        "expected_frontier": expected,
        "actor": {
            "actor_id": prepared.author.actor_id,
            "actor_type": prepared.author.actor_type.value,
        },
        "client": {
            "integration": request.client.integration.value,
            "kind": request.client.kind.value,
            "version": request.client.version,
        },
        "event_drafts": tuple(
            {
                "event_id": item.draft.event_id,
                "schema": {
                    "name": item.draft.schema.name,
                    "version": item.draft.schema.version,
                },
                "occurred_at": item.draft.occurred_at.wire,
                "causal_parents": item.draft.causal_parents,
                "artifact_refs": item.draft.artifact_refs,
                "evidence_refs": item.draft.evidence_refs,
                "payload_commitment": commitment,
            }
            for item, commitment in zip(prepared.drafts, commitments, strict=True)
        ),
    }


def _decode_stored_append_result(data: bytes) -> AppendResult:
    try:
        source = _mapping(strict_json_parse(data))
        if frozenset(source) != frozenset(
            {"accepted", "subject_frontier", "result_frontier", "warnings"}
        ):
            raise ProtocolValueError("stored_result_shape_invalid")
        accepted = tuple(
            AcceptedEventSummary(
                cast(str, _field(item, "event_id")),
                int(cast(str, _field(item, "ingestion_sequence"))),
                int(cast(str, _field(item, "writer_sequence"))),
                cast(str, _field(item, "entry_digest")),
                cast(
                    Literal["projected", "unknown_unprojected"],
                    _field(item, "projection_status"),
                ),
            )
            for item in (_mapping(value) for value in _tuple_field(source, "accepted"))
        )
        warnings = tuple(
            sorted(
                (AppendWarning(cast(str, value)) for value in _tuple_field(source, "warnings")),
                key=lambda item: item.value.encode("ascii"),
            )
        )
        return AppendResult(
            "replayed",
            accepted,
            frontier_from_json(_field(source, "subject_frontier")),
            frontier_from_json(_field(source, "result_frontier")),
            warnings,
        )
    except (TypeError, ValueError) as exc:
        raise _error(
            PublicErrorCode.STORAGE_CORRUPT,
            "The stored operation result is invalid.",
        ) from exc


async def _preflight_replay(
    runtime: TaskRuntime,
    request: PublishWorkRequestModel,
    digest: str,
) -> AppendResult | None:
    operation = await runtime.ledger.lookup_operation(request.writer_id, request.request_id)
    if operation is None:
        return None
    if (
        operation.request_digest != digest
        or operation.operation_kind is not OperationKind.PUBLISH_WORK
    ):
        raise _error(
            PublicErrorCode.IDEMPOTENCY_CONFLICT,
            "The request ID was already used.",
        )
    if operation.state is OperationState.COMPLETE:
        assert operation.result_canonical is not None
        return _decode_stored_append_result(operation.result_canonical)
    if operation.state is OperationState.QUARANTINED:
        raise _error(
            PublicErrorCode.STORAGE_CORRUPT,
            "The stored operation is quarantined.",
        )
    raise _error(
        PublicErrorCode.OPERATION_PENDING,
        "The operation is still pending.",
        retryable=True,
    )


async def _load_accepted_records(
    runtime: TaskRuntime,
    result: AppendResult,
) -> tuple[LedgerRecord, ...]:
    first = min(item.ingestion_sequence for item in result.accepted)
    last = max(item.ingestion_sequence for item in result.accepted)
    records = tuple(
        [
            record
            async for record in runtime.ledger.load_events(
                runtime.session_id,
                after=first - 1,
                through=last,
            )
        ]
    )
    summaries = {item.event_id: item for item in result.accepted}
    matching = tuple(record for record in records if record.event_id in summaries)
    if len(matching) != len(result.accepted):
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The accepted event range is invalid.")
    by_id = {str(record.event_id): record for record in matching}
    ordered = tuple(by_id[item.event_id] for item in result.accepted)
    for record, summary in zip(ordered, result.accepted, strict=True):
        if (
            record.ledger.ingestion_sequence != summary.ingestion_sequence
            or record.writer.sequence != summary.writer_sequence
            or record.entry_digest != summary.entry_digest
            or ("unknown_unprojected" if type(record) is UnknownEvent else "projected")
            != summary.projection_status
        ):
            raise _error(PublicErrorCode.STORAGE_CORRUPT, "The accepted event range is invalid.")
    return ordered


def _accepted_model(record: LedgerRecord) -> PublishWorkAcceptedEventModel:
    return PublishWorkAcceptedEventModel(
        event_id=record.event_id,
        schema_name=record.schema.name,
        schema_version=record.schema.version,
        writer_sequence=str(record.writer.sequence),
        ingestion_sequence=str(record.ledger.ingestion_sequence),
        accepted_at=record.ledger.accepted_at.wire,
        predecessor_digest=record.ledger.previous_entry_digest,
        entry_digest=record.entry_digest,
        projection_status=("unknown_unprojected" if type(record) is UnknownEvent else "projected"),
    )


async def _internal_result(
    request: PublishWorkRequestModel,
    runtime: TaskRuntime,
    prepared: PreparedPublication,
    result: AppendResult,
    digest: str,
) -> PublishWorkInternalResult:
    expected_ids = tuple(item.draft.event_id for item in prepared.drafts)
    if tuple(item.event_id for item in result.accepted) != expected_ids:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The accepted event set is invalid.")
    expected_warnings = (
        (AppendWarning.UNKNOWN_EVENT_SCHEMA_PRESERVED,)
        if any(item.projection_status == "unknown_unprojected" for item in prepared.drafts)
        else ()
    )
    if result.warnings != expected_warnings:
        raise _error(PublicErrorCode.STORAGE_CORRUPT, "The accepted warnings are invalid.")
    records = await _load_accepted_records(runtime, result)
    return PublishWorkInternalResult(
        protocol_version="0.1",
        schema_version="1.0.0",
        request_id=request.request_id,
        request_digest=digest,
        ok=True,
        outcome=result.outcome,
        task_id=runtime.task_id,
        session_id=runtime.session_id,
        writer_id=cast(str, runtime.writer_id),
        subject_frontier=result.subject_frontier,
        result_frontier=result.result_frontier,
        accepted_events=tuple(_accepted_model(record) for record in records),
        warning_codes=tuple(item.value for item in result.warnings),
        coverage=prepared.coverage,
        gaps=prepared.coverage.known_gaps,
        versions=PublishWorkVersionSliceModel(
            protocol_version="0.1",
            engine_version=runtime.engine_version,
            projection_version=runtime.projection_version,
            policy_packs=_POLICY_PACKS,
        ),
    )


async def execute_publish_work(
    app: Application,
    request: PublishWorkRequestModel,
) -> PublishWorkInternalResult:
    """Publish one all-or-nothing batch behind the ready application facade."""

    request_bytes = canonical_encode(
        cast(
            JsonValue,
            request.model_dump(mode="json", by_alias=True, exclude_none=False),
        )
    )
    if len(request_bytes) > MAX_CANONICAL_REQUEST_BYTES:
        raise _error(PublicErrorCode.LIMIT_EXCEEDED, "The request is too large.")

    channel = _channel_for(request)
    runtime = await app.runtime.route(
        RouteCommand(
            request.session_id,
            request.writer_id,
            RouteAccess.WRITE,
            frozenset({RuntimeCapability.WRITE}),
        )
    )
    try:
        if runtime.session_id != request.session_id or runtime.writer_id != request.writer_id:
            raise _error(PublicErrorCode.SESSION_CONFLICT, "The writer route is inconsistent.")
        prepared = prepare_publication(request, channel=channel, app=app)

        commitments = tuple(
            [
                await runtime.objects.commitment_for(item.payload_bytes, ObjectKind.EVENT_PAYLOAD)
                for item in prepared.drafts
            ]
        )
        digest = request_digest(_request_identity(request, prepared, commitments))
        append_result = await _preflight_replay(runtime, request, digest)
        if append_result is None:
            refs: list[ObjectRef] = []
            entries: list[AppendEntry] = []
            for item, commitment in zip(prepared.drafts, commitments, strict=True):
                metadata = ObjectMetadata(
                    ObjectKind.EVENT_PAYLOAD,
                    media_type_for(item.draft.schema.name),
                    runtime.task_id,
                    app.clock.now_utc(),
                )
                staged = await runtime.objects.stage(
                    ObjectSource(data=item.payload_bytes, declared_size=len(item.payload_bytes)),
                    metadata,
                )
                if staged.commitment != commitment:
                    raise _error(
                        PublicErrorCode.STORAGE_CORRUPT,
                        "The staged object commitment is inconsistent.",
                    )
                ref = await runtime.objects.finalize(staged)
                refs.append(ref)
                entries.append(
                    AppendEntry(
                        item.draft,
                        prepared.author,
                        ref,
                        commitment,
                        metadata.media_type,
                        ref.plaintext_size,
                        prepared.channel,
                        prepared.coverage,
                        item.projection_status,
                    )
                )
            expected_frontier = (
                None
                if request.expected_frontier is None
                else int(request.expected_frontier.sequence)
            )
            command = AppendCommand(
                runtime.task_id,
                runtime.session_id,
                cast(str, runtime.writer_id),
                request.request_id,
                OperationKind.PUBLISH_WORK,
                digest,
                expected_frontier,
                tuple(entries),
            )
            mutation = PreparedMutation(
                command.writer_id,
                command.operation_id,
                command.request_digest,
                command.expected_frontier,
                tuple(refs),
                command,
            )
            append_result = await run_prepared_append(runtime.ledger, mutation)
        # The batch is durable from here on. Bounded PublicOperationError values (STORAGE_CORRUPT
        # and friends) stay exactly as raised because they already describe the stored state
        # truthfully. Only an unexpected failure is reshaped, so that assembling the response can
        # never present an accepted write as a non-retryable failure.
        try:
            return await _internal_result(request, runtime, prepared, append_result, digest)
        except PublicOperationError:
            raise
        except Exception as exc:
            # OPERATION_PENDING is the existing "durably underway, resolve by replaying the same
            # request_id" signal, and _preflight_replay returns the stored result on that retry.
            # A fallback INTERNAL_ERROR here would both break the module's closed code inventory
            # and tell the caller the batch failed.
            raise _error(
                PublicErrorCode.OPERATION_PENDING,
                (
                    "The event batch was accepted, but its response could not be assembled. "
                    "Retry with the same request_id to load the stored result."
                ),
                retryable=True,
                reason_code="response_projection_failed",
            ) from exc
    finally:
        await app.runtime.release(runtime)
