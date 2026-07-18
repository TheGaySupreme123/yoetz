"""Decode the frozen replay fixtures into exact accepted-record values."""

from __future__ import annotations

from typing import Any, cast

from fixture_loader import load_fixture_json
from yoetz.domain.events import (
    PAYLOAD_TYPES,
    AcceptedEvent,
    EventSchema,
    LedgerChain,
    LedgerRecord,
    PayloadRef,
    ProjectionLocator,
    RedactionState,
    UnknownEvent,
    WriterChain,
    decode_payload,
)
from yoetz.domain.values import (
    Actor,
    ActorType,
    actor_id,
    event_id,
    freeze_json,
    object_id,
    request_id,
    session_id,
    task_id,
    timestamp_from_string,
    writer_id,
)
from yoetz.protocol.canonical import JsonValue as CanonicalJsonValue
from yoetz.protocol.coverage import (
    AuthorshipAssurance,
    Coverage,
    PublicationChannel,
    coverage_from_json,
)


def _schema(row: dict[str, Any]) -> EventSchema:
    envelope = cast(dict[str, Any], row["envelope"])
    wire = cast(dict[str, Any], envelope["schema"])
    return EventSchema(cast(str, wire["name"]), cast(str, wire["version"]))


def _locator(row: dict[str, Any], schema: EventSchema) -> ProjectionLocator:
    envelope = cast(dict[str, Any], row["envelope"])
    payload = cast(dict[str, Any], row["payload"])
    family = schema.name
    if family in {"plan_published", "plan_revised"}:
        logical_key: str | None = str(payload["plan_version"])
    elif family == "obligation_published":
        logical_key = cast(str, payload["obligation_id"])
    elif family in {"assignment_recorded", "decision_recorded", "check_recorded"}:
        logical_key = cast(str, envelope["event_id"])
    elif family == "action_recorded":
        logical_key = cast(str, payload["action_id"])
    elif family == "result_recorded":
        logical_key = cast(str, payload["result_id"])
    elif family == "evidence_recorded":
        logical_key = cast(str, payload["evidence_id"])
    elif family == "claim_recorded":
        logical_key = cast(str, payload["claim_id"])
    elif family in {"finding_recorded", "response_recorded"}:
        logical_key = cast(str, payload["finding_id"])
    else:
        logical_key = None
    if family == "redaction_recorded":
        event_targets = tuple(cast(list[str], payload["target_event_ids"]))
        object_targets = tuple(cast(list[str], payload["target_object_ids"]))
    else:
        event_targets = ()
        object_targets = ()
    return ProjectionLocator(
        schema=schema,
        logical_key=logical_key,
        canonical_payload_digest=cast(str, row["canonical_payload_digest"]),
        redaction_target_event_ids=cast(Any, event_targets),
        redaction_target_object_ids=cast(Any, object_targets),
    )


def _coverage(value: object) -> Coverage:
    return coverage_from_json(cast(CanonicalJsonValue, freeze_json(value)))


def _record(row: dict[str, Any]) -> LedgerRecord:
    envelope = cast(dict[str, Any], row["envelope"])
    author = cast(dict[str, Any], envelope["author"])
    writer = cast(dict[str, Any], envelope["writer"])
    ledger = cast(dict[str, Any], envelope["ledger"])
    payload_ref = cast(dict[str, Any], envelope["payload_ref"])
    schema = _schema(row)
    common: dict[str, Any] = {
        "event_id": event_id(envelope["event_id"]),
        "task_id": task_id(envelope["task_id"]),
        "session_id": session_id(envelope["session_id"]),
        "schema": schema,
        "author": Actor(
            actor_id(author["actor_id"]),
            ActorType(author["actor_type"]),
            AuthorshipAssurance(author["assurance"]),
        ),
        "writer": WriterChain(
            writer_id(writer["writer_id"]),
            int(writer["sequence"]),
            cast(str, writer["previous_entry_digest"]),
        ),
        "ledger": LedgerChain(
            int(ledger["ingestion_sequence"]),
            cast(str, ledger["previous_entry_digest"]),
            timestamp_from_string(ledger["accepted_at"]),
        ),
        "operation_id": request_id(envelope["operation_id"]),
        "occurred_at": timestamp_from_string(envelope["occurred_at"]),
        "causal_parents": cast(Any, tuple(cast(list[str], envelope["causal_parents"]))),
        "publication_channel": PublicationChannel(envelope["publication_channel"]),
        "coverage": _coverage(envelope["coverage"]),
        "payload_ref": PayloadRef(
            object_id(payload_ref["object_id"]),
            cast(str, payload_ref["media_type"]),
            cast(int, payload_ref["plaintext_size"]),
            cast(str, payload_ref["commitment"]),
        ),
        "redaction": RedactionState(envelope["redaction"]),
        "artifact_refs": cast(Any, tuple(cast(list[str], envelope["artifact_refs"]))),
        "evidence_refs": cast(Any, tuple(cast(list[str], envelope["evidence_refs"]))),
        "entry_digest": cast(str, envelope["entry_digest"]),
    }
    if schema in PAYLOAD_TYPES:
        return AcceptedEvent(
            **common,
            payload=decode_payload(schema, freeze_json(row["payload"])),
            projection_locator=_locator(row, schema),
        )
    digest = cast(str, row["canonical_payload_digest"])
    return UnknownEvent(
        **common,
        payload=freeze_json(row["payload"]),
        projection_locator=ProjectionLocator(schema, None, digest),
        canonical_payload_digest=digest,
    )


def replay_records(name: str) -> tuple[LedgerRecord, ...]:
    """Return one frozen replay vector's accepted prefix."""

    document = cast(
        dict[str, Any],
        load_fixture_json(f"replay/{name}.case.json"),
    )
    raw_input = cast(dict[str, Any], document["input"])
    rows = cast(list[dict[str, Any]], raw_input["accepted_entries"])
    return tuple(_record(row) for row in rows)
