"""SQLite implementation of the authoritative task-ledger boundary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast

import apsw

from yoetz.adapters.memory.ledger import (
    MemoryLedgerAdapter,
    MemoryLedgerState,
    build_append_operation_record,
)
from yoetz.domain.events import (
    AcceptedEvent,
    CheckRecordedPayload,
    EventSchema,
    LedgerChain,
    LedgerRecord,
    PayloadRef,
    ProjectionLocator,
    RedactionState,
    UnknownEvent,
    WriterChain,
    accepted_record_to_json,
    decode_payload,
)
from yoetz.domain.findings import Finding, RankedFindings, SemanticProvenance
from yoetz.domain.values import (
    Actor,
    ActorType,
    EventId,
    Frontier,
    ObjectId,
    actor_id,
    format_rfc3339_millis,
    frontier_from_json,
    object_id,
    parse_rfc3339_millis,
    timestamp_from_datetime,
    writer_id,
)
from yoetz.domain.values import (
    JsonValue as DomainJsonValue,
)
from yoetz.kernel.deterministic_checks import CaseAvailabilityFacts
from yoetz.kernel.projections import PROJECTION_VERSION, ProjectionState, projection_digest
from yoetz.kernel.reducers import replay
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import (
    AcceptedEventSummary,
    AppendCommand,
    AppendResult,
    AppendWarning,
    AttemptOutcome,
    CheckCommitResult,
    CheckPhase,
    CheckPolicyExecution,
    CheckVersionSlice,
    FrozenCase,
    OperationKind,
    OperationLease,
    OperationRecord,
    OperationResultLocator,
    OperationState,
    PendingVerdict,
    ProjectionPage,
    ProjectionQuery,
    ProjectionView,
    SelectedAttempt,
    SemanticAttemptHandle,
    SemanticJobRecord,
    StoredProjection,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectStorePort
from yoetz.ports.runtime import OwnershipFence
from yoetz.protocol.canonical import canonical_encode, strict_json_parse
from yoetz.protocol.coverage import (
    AuthorshipAssurance,
    PublicationChannel,
    coverage_from_json,
    coverage_to_json,
)
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import SemanticReason, SemanticStatus

__all__ = ["CheckpointReport", "SqliteLedger"]

_GENESIS_DIGEST: Final = "genesis"


def _public_error(code: PublicErrorCode, *, retryable: bool = False) -> PublicOperationError:
    return PublicOperationError(code, code.value.lower(), retryable)


@dataclass(frozen=True, slots=True)
class CheckpointReport:
    busy: int
    log: int
    checkpointed: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.busy, self.log, self.checkpointed)
        ):
            raise ValueError("checkpoint_report_invalid")


@dataclass(frozen=True, slots=True)
class _Reservation:
    source_identity_digest: str
    publication_ordinal: int


class _SqliteImportShim:
    """Lets the memory oracle prepare a candidate after SQLite has verified W-C-001."""

    reservation: _Reservation | None = None

    def has_pending_import(self, session_id: str) -> bool:
        del session_id
        return False

    def publication_reservation(self, writer_id: str, request_id: str) -> _Reservation | None:
        del writer_id, request_id
        return self.reservation


def _head(db: apsw.Connection) -> Frontier:
    row = db.execute(
        "SELECT ingestion_seq, entry_digest FROM events ORDER BY ingestion_seq DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return Frontier.genesis()
    if type(row[0]) is not int or type(row[1]) is not str:
        raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
    return Frontier(row[0], row[1])


def _operation_digest_row(
    db: apsw.Connection, writer_id: str, operation_id: str
) -> tuple[str, str] | None:
    row = db.execute(
        "SELECT request_digest, state FROM operations WHERE writer_id=? AND operation_id=?",
        (writer_id, operation_id),
    ).fetchone()
    if row is None:
        return None
    if type(row[0]) is not str or type(row[1]) is not str:
        raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
    return row[0], row[1]


def _import_reservation(db: apsw.Connection, command: AppendCommand) -> _Reservation:
    row = db.execute(
        "SELECT ipr.source_identity_digest, ipr.publication_ordinal, "
        "j.session_id, j.publishing_writer_id, j.state, j.phase, j.batch_count, "
        "j.report_request_id, j.report_event_id "
        "FROM import_publication_requests AS ipr "
        "JOIN import_jobs AS j USING(source_identity_digest) "
        "WHERE ipr.publishing_writer_id=? AND ipr.request_id=?",
        (command.writer_id, command.operation_id),
    ).fetchone()
    if row is None or len(row) != 9:
        raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
    source, ordinal, session, publisher, state, phase, count, report_request, report_event = row
    if (
        type(source) is not str
        or type(ordinal) is not int
        or session != command.session_id
        or publisher != command.writer_id
        or type(count) is not int
        or ordinal < 0
        or ordinal > count
        or state not in {"pending", "complete"}
        or type(phase) is not str
    ):
        raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
    event_ids = tuple(entry.draft.event_id for entry in command.entries)
    if ordinal < count:
        batch = db.execute(
            "SELECT state, request_id, event_ids_canonical, event_count "
            "FROM import_batches WHERE source_identity_digest=? AND batch_index=?",
            (source, ordinal),
        ).fetchone()
        if batch is None or len(batch) != 4:
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
        batch_state, request_id, encoded_ids, event_count = batch
        if (
            batch_state != "planned"
            or request_id != command.operation_id
            or type(encoded_ids) is not bytes
            or type(event_count) is not int
            or event_count != len(event_ids)
            or encoded_ids != canonical_encode(event_ids)
        ):
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
    elif (
        command.operation_id != report_request
        or len(event_ids) != 1
        or event_ids[0] != report_event
        or phase not in {"report_ready", "report_published", "terminal"}
    ):
        raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
    return _Reservation(source, ordinal)


class SqliteLedger:
    """Durable ledger over one initialized task-bundle connection."""

    def __init__(
        self,
        *,
        db: apsw.Connection,
        task_id: str,
        ownership_fence: OwnershipFence,
        clock: ClockPort | None = None,
        ids: IdPort | None = None,
        objects: ObjectStorePort | None = None,
    ) -> None:
        self._db = db
        self._task_id = task_id
        self._fence = ownership_fence
        self._clock = clock
        self._ids = ids
        self._objects = objects
        self._lock = asyncio.Lock()
        self._state = MemoryLedgerState()
        self._requires_recovery = _head(db).sequence != 0

    def _object_ref_from_inventory(self, object_id: str, task: str, media_type: str) -> ObjectRef:
        row = self._db.execute(
            "SELECT kind,plaintext_size,commitment,envelope_digest,encryption_format,key_slot,"
            "durable_at FROM objects WHERE object_id=?",
            (object_id,),
        ).fetchone()
        if row is None or len(row) != 7:
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
        try:
            kind = ObjectKind(row[0])
            created_at = parse_rfc3339_millis(cast(str, row[6]))
            return ObjectRef(
                object_id,
                cast(int, row[1]),
                cast(str, row[2]),
                cast(str, row[3]),
                cast(Literal["yoetz-object/1"], row[4]),
                cast(str, row[5]),
                ObjectMetadata(kind, media_type, task, created_at),
            )
        except (TypeError, ValueError) as exc:
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT) from exc

    async def _decode_durable_record(self, row: tuple[object, ...]) -> LedgerRecord:
        if len(row) != 5 or type(row[0]) is not bytes:
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
        canonical, status, logical_key, payload_digest, _ = row
        try:
            parsed = strict_json_parse(cast(bytes, canonical))
            if canonical_encode(parsed) != canonical or not isinstance(parsed, Mapping):
                raise ValueError("canonical_entry_invalid")
            source = cast(Mapping[str, object], parsed)
            schema_source = cast(Mapping[str, object], source["schema"])
            author_source = cast(Mapping[str, object], source["author"])
            writer_source = cast(Mapping[str, object], source["writer"])
            ledger_source = cast(Mapping[str, object], source["ledger"])
            payload_source = cast(Mapping[str, object], source["payload_ref"])
            schema = EventSchema(
                cast(str, schema_source["name"]), cast(str, schema_source["version"])
            )
            actor = Actor(
                actor_id(cast(str, author_source["actor_id"])),
                ActorType(cast(str, author_source["actor_type"])),
                AuthorshipAssurance(cast(str, author_source["assurance"])),
            )
            writer = WriterChain(
                writer_id(cast(str, writer_source["writer_id"])),
                int(cast(str, writer_source["sequence"])),
                cast(str, writer_source["previous_entry_digest"]),
            )
            ledger = LedgerChain(
                int(cast(str, ledger_source["ingestion_sequence"])),
                cast(str, ledger_source["previous_entry_digest"]),
                timestamp_from_datetime(
                    parse_rfc3339_millis(cast(str, ledger_source["accepted_at"]))
                ),
            )
            payload_ref = PayloadRef(
                object_id(cast(str, payload_source["object_id"])),
                cast(str, payload_source["media_type"]),
                cast(int, payload_source["plaintext_size"]),
                cast(str, payload_source["commitment"]),
                cast(Literal["yoetz-object/1"], payload_source["encryption_format"]),
            )
            target_events_raw = strict_json_parse(cast(bytes, row[4]))
            target_objects_row = self._db.execute(
                "SELECT redaction_target_object_ids FROM event_projection_locators "
                "WHERE event_id=?",
                (cast(str, source["event_id"]),),
            ).fetchone()
            if target_objects_row is None or type(target_objects_row[0]) is not bytes:
                raise ValueError("locator_missing")
            target_objects_raw = strict_json_parse(target_objects_row[0])
            locator = ProjectionLocator(
                schema,
                cast(str | None, logical_key),
                cast(str, payload_digest),
                cast(
                    tuple[EventId, ...],
                    tuple(cast(list[str] | tuple[str, ...], target_events_raw)),
                ),
                cast(
                    tuple[ObjectId, ...],
                    tuple(cast(list[str] | tuple[str, ...], target_objects_raw)),
                ),
            )
            ref = self._object_ref_from_inventory(
                payload_ref.object_id, cast(str, source["task_id"]), payload_ref.media_type
            )
            self._state.object_refs[ref.object_id] = ref
            payload = None
            if source["redaction"] == "present" and self._objects is not None:
                try:
                    chunks = [chunk async for chunk in self._objects.open_verified(ref)]
                except KeyError, OSError, ValueError:
                    chunks = []
                if chunks:
                    payload_json = strict_json_parse(b"".join(chunks))
                    payload = (
                        payload_json
                        if status == "unknown_unprojected"
                        else decode_payload(schema, cast(DomainJsonValue, payload_json))
                    )
            common: Any = dict(
                event_id=cast(str, source["event_id"]),
                task_id=cast(str, source["task_id"]),
                session_id=cast(str, source["session_id"]),
                schema=schema,
                author=actor,
                writer=writer,
                ledger=ledger,
                operation_id=cast(str, source["operation_id"]),
                occurred_at=timestamp_from_datetime(
                    parse_rfc3339_millis(cast(str, source["occurred_at"]))
                ),
                causal_parents=tuple(cast(list[str] | tuple[str, ...], source["causal_parents"])),
                publication_channel=PublicationChannel(cast(str, source["publication_channel"])),
                coverage=coverage_from_json(cast(DomainJsonValue, source["coverage"])),
                payload_ref=payload_ref,
                redaction=RedactionState(cast(str, source["redaction"])),
                artifact_refs=tuple(cast(list[str] | tuple[str, ...], source["artifact_refs"])),
                evidence_refs=tuple(cast(list[str] | tuple[str, ...], source["evidence_refs"])),
                entry_digest=cast(str, source["entry_digest"]),
                payload=payload,
                projection_locator=locator,
            )
            record: LedgerRecord
            if status == "unknown_unprojected":
                record = UnknownEvent(**common, canonical_payload_digest=cast(str, payload_digest))
            else:
                record = AcceptedEvent(**common)
            if canonical_encode(accepted_record_to_json(record)) != canonical:
                raise ValueError("canonical_index_mismatch")
            return record
        except PublicOperationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT) from exc

    async def _ensure_recovered(self) -> None:
        if not self._requires_recovery:
            return
        async with self._lock:
            if not self._requires_recovery:
                return
            rows = cast(
                list[tuple[object, ...]],
                self._db.execute(
                    "SELECT e.canonical_entry,e.projection_status,l.logical_key,"
                    "l.canonical_payload_digest,l.redaction_target_event_ids "
                    "FROM events AS e JOIN event_projection_locators AS l USING(event_id) "
                    "ORDER BY e.ingestion_seq"
                ).fetchall(),
            )
            records = tuple([await self._decode_durable_record(row) for row in rows])
            try:
                projection = replay(records)
            except ValueError as exc:
                raise _public_error(PublicErrorCode.STORAGE_CORRUPT) from exc
            for (
                writer_id_value,
                task_value,
                session_value,
                next_seq,
                head_digest,
            ) in self._db.execute(
                "SELECT writer_id,task_id,session_id,next_writer_seq,head_entry_digest FROM writers"
            ):
                self._state.restore_writer(
                    cast(str, writer_id_value),
                    cast(str, task_value),
                    cast(str, session_value),
                    cast(int, next_seq),
                    cast(str, head_digest),
                )
            self._state.records = records
            self._state.projection = projection
            for operation_row in self._db.execute(
                "SELECT writer_id,operation_id,operation_kind,request_digest,state,phase,"
                "result_canonical,result_digest,first_ingestion_seq,last_ingestion_seq,terminal_at "
                "FROM operations WHERE state='complete'"
            ):
                (
                    writer_value,
                    operation_value,
                    kind_value,
                    digest_value,
                    state_value,
                    phase_value,
                    result_canonical,
                    result_digest,
                    first,
                    last,
                    terminal,
                ) = operation_row
                structural = tuple(
                    item[0]
                    for item in self._db.execute(
                        "SELECT event_id FROM events WHERE ingestion_seq BETWEEN ? AND ? "
                        "ORDER BY event_id",
                        (first, last),
                    )
                )
                locator = OperationResultLocator(first, last, None, structural)
                operation = OperationRecord(
                    writer_value,
                    operation_value,
                    OperationKind(kind_value),
                    digest_value,
                    OperationState(state_value),
                    CheckPhase(phase_value),
                    None,
                    None,
                    None,
                    None,
                    None,
                    result_canonical,
                    result_digest,
                    locator,
                    None,
                    parse_rfc3339_millis(terminal),
                )
                append_result = None
                check_result = None
                if operation.operation_kind is not OperationKind.CHECK:
                    result_json = strict_json_parse(result_canonical)
                    result_map = cast(Mapping[str, object], result_json)
                    accepted_rows = cast(list[Mapping[str, object]], result_map["accepted"])
                    append_result = AppendResult(
                        "accepted",
                        tuple(
                            AcceptedEventSummary(
                                cast(str, item["event_id"]),
                                int(cast(str, item["ingestion_sequence"])),
                                int(cast(str, item["writer_sequence"])),
                                cast(str, item["entry_digest"]),
                                cast(
                                    Literal["projected", "unknown_unprojected"],
                                    item["projection_status"],
                                ),
                            )
                            for item in accepted_rows
                        ),
                        frontier_from_json(result_map["subject_frontier"]),
                        frontier_from_json(result_map["result_frontier"]),
                        tuple(
                            AppendWarning(cast(str, value))
                            for value in cast(list[object], result_map["warnings"])
                        ),
                    )
                else:
                    operation_records = records[cast(int, first) - 1 : cast(int, last)]
                    check_event = next(
                        (
                            item
                            for item in reversed(operation_records)
                            if type(item) is AcceptedEvent
                            and type(item.payload) is CheckRecordedPayload
                        ),
                        None,
                    )
                    if check_event is not None:
                        check_payload = cast(CheckRecordedPayload, check_event.payload)
                        finding_payloads = tuple(
                            item.payload
                            for item in operation_records
                            if type(item) is AcceptedEvent
                            and item.schema.name == "finding_recorded"
                            and item.payload is not None
                        )
                        executions = tuple(
                            CheckPolicyExecution(
                                item.policy_id,
                                item.policy_version,
                                item.outcome,
                                item.reason,
                            )
                            for item in check_payload.policy_executions
                        )
                        check_result = CheckCommitResult(
                            "committed",
                            check_event.task_id,
                            check_event.session_id,
                            check_event.writer.writer_id,
                            check_event.operation_id,
                            check_payload.subject_frontier,
                            Frontier(
                                operation_records[-1].ledger.ingestion_sequence,
                                operation_records[-1].entry_digest,
                            ),
                            check_payload.verdict,
                            cast(tuple[Finding, ...], finding_payloads),
                            check_payload.suppressed_count,
                            executions,
                            check_payload.semantic_status,
                            check_payload.semantic_reason,
                            check_payload.semantic_provenance,
                            check_payload.coverage,
                            CheckVersionSlice(
                                "0.1",
                                check_payload.engine_version,
                                check_payload.projection_version,
                                tuple(
                                    f"{item.policy_id}/{item.policy_version}" for item in executions
                                ),
                            ),
                        )
                self._state.operations[(writer_value, operation_value)] = (
                    operation,
                    append_result,
                )
                if check_result is not None:
                    self._state.check_results[(writer_value, operation_value)] = check_result
            self._requires_recovery = False

    def _inventory_object(self, ref: ObjectRef) -> None:
        existing = self._db.execute(
            "SELECT kind,plaintext_size,commitment,envelope_digest,encryption_format,key_slot "
            "FROM objects WHERE object_id=?",
            (ref.object_id,),
        ).fetchone()
        descriptor = (
            ref.metadata.kind.value,
            ref.plaintext_size,
            ref.commitment,
            ref.envelope_digest,
            ref.encryption_format,
            ref.key_slot,
        )
        if existing is None:
            self._db.execute(
                "INSERT INTO objects(object_id,kind,plaintext_size,commitment,envelope_digest,"
                "encryption_format,key_slot,state,durable_at) VALUES(?,?,?,?,?,?,?,'present',?)",
                (ref.object_id, *descriptor, format_rfc3339_millis(ref.metadata.created_at)),
            )
        elif tuple(existing) != descriptor:
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT)

    def _sync_runtime_state(self) -> None:
        now = format_rfc3339_millis(
            self._clock.now_utc() if self._clock is not None else datetime.now(UTC)
        )
        for key, (record, _) in self._state.operations.items():
            if record.resume_object_ref is not None:
                self._inventory_object(record.resume_object_ref)
            if (
                record.result_locator is not None
                and record.result_locator.result_object_ref is not None
            ):
                self._inventory_object(record.result_locator.result_object_ref)
            exists = self._db.execute(
                "SELECT 1 FROM operations WHERE writer_id=? AND operation_id=?", key
            ).fetchone()
            locator = record.result_locator
            values = (
                record.operation_kind.value,
                record.request_digest,
                None if record.resume_object_ref is None else record.resume_object_ref.object_id,
                record.state.value,
                record.phase.value,
                record.owner_generation,
                record.lease_owner_id,
                record.lease_generation,
                None
                if record.lease_expires_at is None
                else format_rfc3339_millis(record.lease_expires_at),
                None if locator is None else locator.first_ingestion_sequence,
                None if locator is None else locator.last_ingestion_sequence,
                record.result_canonical,
                record.result_digest,
                None
                if locator is None or locator.result_object_ref is None
                else locator.result_object_ref.object_id,
                None if record.quarantine_code is None else record.quarantine_code.value,
                None if record.terminal_at is None else format_rfc3339_millis(record.terminal_at),
            )
            if exists is None:
                self._db.execute(
                    "INSERT INTO operations(writer_id,operation_id,operation_kind,request_digest,"
                    "resume_object_id,state,phase,owner_generation,lease_owner_id,lease_generation,"
                    "lease_expires_at,first_ingestion_seq,last_ingestion_seq,result_canonical,"
                    "result_digest,result_object_id,quarantine_code,terminal_at,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key[0], key[1], *values, now, now),
                )
            else:
                self._db.execute(
                    "UPDATE operations SET operation_kind=?,request_digest=?,resume_object_id=?,"
                    "state=?,phase=?,owner_generation=?,lease_owner_id=?,lease_generation=?,"
                    "lease_expires_at=?,first_ingestion_seq=?,last_ingestion_seq=?,"
                    "result_canonical=?,result_digest=?,result_object_id=?,quarantine_code=?,"
                    "terminal_at=?,updated_at=? WHERE writer_id=? AND operation_id=?",
                    (*values, now, key[0], key[1]),
                )
        for ref in self._state.phase_objects.values():
            self._inventory_object(ref)
        for job in self._state.jobs.values():
            self._inventory_object(job.case_object_ref)
            if job.selected_result_object_ref is not None:
                self._inventory_object(job.selected_result_object_ref)
            self._db.execute(
                "INSERT OR REPLACE INTO semantic_jobs(job_id,writer_id,operation_id,case_digest,"
                "case_object_id,state,active_attempt_id,selected_attempt_id,attempt_count,"
                "owner_generation,lease_owner_id,lease_generation,lease_expires_at,"
                "selected_result_object_id,terminal_code,terminal_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    job.job_id,
                    job.writer_id,
                    job.operation_id,
                    job.case_digest,
                    job.case_object_ref.object_id,
                    job.state,
                    job.active_attempt_id,
                    job.selected_attempt_id,
                    job.attempt_count,
                    str(self._fence.owner_generation) if job.state == "leased" else None,
                    job.lease_owner_id,
                    job.lease_generation,
                    None
                    if job.lease_expires_at is None
                    else format_rfc3339_millis(job.lease_expires_at),
                    None
                    if job.selected_result_object_ref is None
                    else job.selected_result_object_ref.object_id,
                    None if job.terminal_code is None else job.terminal_code.value,
                    None if job.terminal_at is None else format_rfc3339_millis(job.terminal_at),
                    now,
                    now,
                ),
            )
        for attempt in self._state.attempts.values():
            if attempt.result_object_ref is not None:
                self._inventory_object(attempt.result_object_ref)
            handle = attempt.handle
            self._db.execute(
                "INSERT OR REPLACE INTO semantic_attempts(attempt_id,job_id,attempt_ordinal,"
                "provider_request_id,owner_generation,lease_owner_id,lease_generation,state,"
                "result_object_id,terminal_code,started_at,terminal_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    handle.attempt_id,
                    handle.job_id,
                    handle.attempt_ordinal,
                    handle.provider_request_id,
                    handle.owner_generation,
                    handle.lease_owner_id,
                    handle.lease_generation,
                    attempt.state,
                    None
                    if attempt.result_object_ref is None
                    else attempt.result_object_ref.object_id,
                    None if attempt.terminal_code is None else attempt.terminal_code.value,
                    now,
                    now if attempt.state in {"selected", "failed", "expired", "late"} else None,
                ),
            )

    def _verify_owner(self) -> None:
        rows = dict(
            cast(
                list[tuple[str, str]],
                self._db.execute(
                    "SELECT key, value FROM bundle_meta WHERE key IN ('owner_generation','owner_nonce')"
                ).fetchall(),
            )
        )
        stored_generation = rows.get("owner_generation")
        stored_nonce = rows.get("owner_nonce")
        if stored_generation is not None and stored_generation != str(self._fence.owner_generation):
            raise _public_error(PublicErrorCode.STORAGE_UNSAFE)
        if stored_nonce is not None and stored_nonce != self._fence.nonce:
            raise _public_error(PublicErrorCode.STORAGE_UNSAFE)

    def _ensure_writer(self, command: AppendCommand, now: str) -> None:
        row = self._db.execute(
            "SELECT task_id, session_id FROM writers WHERE writer_id=?",
            (command.writer_id,),
        ).fetchone()
        if row is None:
            self._db.execute(
                "INSERT INTO writers(writer_id,task_id,session_id,next_writer_seq,"
                "head_entry_digest,state,created_at) VALUES(?,?,?,1,'genesis','active',?)",
                (command.writer_id, command.task_id, command.session_id, now),
            )
        elif row != (command.task_id, command.session_id):
            raise _public_error(PublicErrorCode.EVENT_INVALID)

    def _persist_append(
        self, command: AppendCommand, result: AppendResult, records: tuple[LedgerRecord, ...]
    ) -> None:
        now = records[-1].ledger.accepted_at.wire
        self._verify_owner()
        self._ensure_writer(command, now)
        current = _head(self._db)
        if current != result.subject_frontier:
            raise _public_error(PublicErrorCode.FRONTIER_CONFLICT)
        for record, entry in zip(records, command.entries, strict=True):
            ref = entry.payload_object
            existing = self._db.execute(
                "SELECT kind,plaintext_size,commitment,envelope_digest,encryption_format,key_slot "
                "FROM objects WHERE object_id=?",
                (ref.object_id,),
            ).fetchone()
            descriptor = (
                ref.metadata.kind.value,
                ref.plaintext_size,
                ref.commitment,
                ref.envelope_digest,
                ref.encryption_format,
                ref.key_slot,
            )
            if existing is None:
                self._db.execute(
                    "INSERT INTO objects(object_id,kind,plaintext_size,commitment,envelope_digest,"
                    "encryption_format,key_slot,state,durable_at) VALUES(?,?,?,?,?,?,?,'present',?)",
                    (ref.object_id, *descriptor, format_rfc3339_millis(ref.metadata.created_at)),
                )
            elif tuple(existing) != descriptor:
                raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
            status = "unknown_unprojected" if type(record) is UnknownEvent else "projected"
            summary = "opaque_unknown" if type(record) is UnknownEvent else record.schema.name
            canonical = canonical_encode(accepted_record_to_json(record))
            self._db.execute(
                "INSERT INTO events(ingestion_seq,event_id,task_id,session_id,schema_name,"
                "schema_version,projection_status,summary_code,author_id,author_type,"
                "author_assurance,writer_id,writer_seq,operation_id,previous_ledger_digest,"
                "previous_writer_digest,entry_digest,canonical_entry,payload_object_id,"
                "payload_commitment,publication_channel,redaction_state,occurred_at,accepted_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.ledger.ingestion_sequence,
                    record.event_id,
                    record.task_id,
                    record.session_id,
                    record.schema.name,
                    record.schema.version,
                    status,
                    summary,
                    record.author.actor_id,
                    record.author.actor_type.value,
                    record.author.assurance.value,
                    record.writer.writer_id,
                    record.writer.sequence,
                    record.operation_id,
                    record.ledger.previous_entry_digest,
                    record.writer.previous_entry_digest,
                    record.entry_digest,
                    canonical,
                    record.payload_ref.object_id,
                    record.payload_ref.commitment,
                    record.publication_channel.value,
                    record.redaction.value,
                    record.occurred_at.wire,
                    record.ledger.accepted_at.wire,
                ),
            )
            locator = record.projection_locator
            self._db.execute(
                "INSERT INTO event_projection_locators(event_id,schema_name,schema_version,"
                "logical_key,canonical_payload_digest,redaction_target_event_ids,"
                "redaction_target_object_ids) VALUES(?,?,?,?,?,?,?)",
                (
                    record.event_id,
                    record.schema.name,
                    record.schema.version,
                    locator.logical_key,
                    locator.canonical_payload_digest,
                    canonical_encode(locator.redaction_target_event_ids),
                    canonical_encode(locator.redaction_target_object_ids),
                ),
            )
            self._db.executemany(
                "INSERT INTO event_parents(child_event_id,parent_event_id) VALUES(?,?)",
                ((record.event_id, parent) for parent in record.causal_parents),
            )
            refs: list[tuple[str, str, str]] = [
                (record.event_id, "artifact", value) for value in record.artifact_refs
            ]
            refs.extend(
                (record.event_id, "evidence" if value.startswith("evd_") else "result", value)
                for value in record.evidence_refs
            )
            if refs:
                self._db.executemany(
                    "INSERT INTO event_refs(event_id,ref_type,target_id) VALUES(?,?,?)", refs
                )
        final = records[-1]
        self._db.execute(
            "UPDATE counters SET next_value=? WHERE name='ingestion_sequence'",
            (final.ledger.ingestion_sequence + 1,),
        )
        self._db.execute(
            "UPDATE writers SET next_writer_seq=?,head_entry_digest=? WHERE writer_id=?",
            (final.writer.sequence + 1, final.entry_digest, command.writer_id),
        )
        operation = build_append_operation_record(
            command, result, datetime.fromisoformat(now.replace("Z", "+00:00"))
        )
        locator = operation.result_locator
        assert operation.result_canonical is not None and operation.result_digest is not None
        assert operation.terminal_at is not None and locator is not None
        self._db.execute(
            "INSERT INTO operations(writer_id,operation_id,operation_kind,request_digest,"
            "resume_object_id,state,phase,owner_generation,lease_owner_id,lease_generation,"
            "lease_expires_at,first_ingestion_seq,last_ingestion_seq,result_canonical,"
            "result_digest,result_object_id,quarantine_code,terminal_at,created_at,updated_at) "
            "VALUES(?,?,?,?,NULL,'complete','terminal',NULL,NULL,NULL,NULL,?,?,?,?,NULL,NULL,?,?,?)",
            (
                command.writer_id,
                command.operation_id,
                command.operation_kind.value,
                command.request_digest,
                locator.first_ingestion_sequence,
                locator.last_ingestion_sequence,
                operation.result_canonical,
                operation.result_digest,
                format_rfc3339_millis(operation.terminal_at),
                now,
                now,
            ),
        )
        projection = self._state.projection
        self._db.execute(
            "UPDATE projection_state SET applied_through_seq=?,state_digest=? "
            "WHERE projection_name='work'",
            (projection.frontier, projection_digest(projection)),
        )
        self._db.execute(
            "UPDATE p1_projection_state SET frontier_seq=?,head_digest=?,"
            "open_obligation_count=?,unresolved_finding_count=?,freshness=?,"
            "unknown_event_count=?,task_title_source_event_id=?,"
            "status_coverage_canonical=?,status_gap_codes_canonical=? "
            "WHERE projection_name='work'",
            (
                projection.frontier,
                projection.head_digest,
                len(projection.obligations),
                len(projection.findings),
                projection.freshness.value,
                projection.unknown_event_count,
                records[0].event_id,
                canonical_encode(coverage_to_json(records[-1].coverage)),
                canonical_encode(projection.coverage_gaps),
            ),
        )

    def _persist_derived_records(self, records: tuple[LedgerRecord, ...]) -> None:
        """Persist already-reduced engine records inside the final check transaction."""

        if not records:
            return
        self._verify_owner()
        head = _head(self._db)
        if (
            records[0].ledger.ingestion_sequence != head.sequence + 1
            or records[0].ledger.previous_entry_digest != head.head_digest
        ):
            raise _public_error(PublicErrorCode.FRONTIER_CONFLICT)
        for record in records:
            ref = self._state.object_refs.get(record.payload_ref.object_id)
            if ref is None:
                raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
            self._inventory_object(ref)
            status = "unknown_unprojected" if type(record) is UnknownEvent else "projected"
            summary = "opaque_unknown" if type(record) is UnknownEvent else record.schema.name
            self._db.execute(
                "INSERT INTO events(ingestion_seq,event_id,task_id,session_id,schema_name,"
                "schema_version,projection_status,summary_code,author_id,author_type,"
                "author_assurance,writer_id,writer_seq,operation_id,previous_ledger_digest,"
                "previous_writer_digest,entry_digest,canonical_entry,payload_object_id,"
                "payload_commitment,publication_channel,redaction_state,occurred_at,accepted_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.ledger.ingestion_sequence,
                    record.event_id,
                    record.task_id,
                    record.session_id,
                    record.schema.name,
                    record.schema.version,
                    status,
                    summary,
                    record.author.actor_id,
                    record.author.actor_type.value,
                    record.author.assurance.value,
                    record.writer.writer_id,
                    record.writer.sequence,
                    record.operation_id,
                    record.ledger.previous_entry_digest,
                    record.writer.previous_entry_digest,
                    record.entry_digest,
                    canonical_encode(accepted_record_to_json(record)),
                    record.payload_ref.object_id,
                    record.payload_ref.commitment,
                    record.publication_channel.value,
                    record.redaction.value,
                    record.occurred_at.wire,
                    record.ledger.accepted_at.wire,
                ),
            )
            locator = record.projection_locator
            self._db.execute(
                "INSERT INTO event_projection_locators(event_id,schema_name,schema_version,"
                "logical_key,canonical_payload_digest,redaction_target_event_ids,"
                "redaction_target_object_ids) VALUES(?,?,?,?,?,?,?)",
                (
                    record.event_id,
                    record.schema.name,
                    record.schema.version,
                    locator.logical_key,
                    locator.canonical_payload_digest,
                    canonical_encode(locator.redaction_target_event_ids),
                    canonical_encode(locator.redaction_target_object_ids),
                ),
            )
            if record.causal_parents:
                self._db.executemany(
                    "INSERT INTO event_parents(child_event_id,parent_event_id) VALUES(?,?)",
                    ((record.event_id, parent) for parent in record.causal_parents),
                )
            refs: list[tuple[str, str, str]] = [
                (record.event_id, "artifact", value) for value in record.artifact_refs
            ]
            refs.extend(
                (record.event_id, "evidence" if value.startswith("evd_") else "result", value)
                for value in record.evidence_refs
            )
            if refs:
                self._db.executemany(
                    "INSERT INTO event_refs(event_id,ref_type,target_id) VALUES(?,?,?)", refs
                )
        final = records[-1]
        self._db.execute(
            "UPDATE counters SET next_value=? WHERE name='ingestion_sequence'",
            (final.ledger.ingestion_sequence + 1,),
        )
        self._db.execute(
            "UPDATE writers SET next_writer_seq=?,head_entry_digest=? WHERE writer_id=?",
            (final.writer.sequence + 1, final.entry_digest, final.writer.writer_id),
        )
        projection = self._state.projection
        self._db.execute(
            "UPDATE projection_state SET applied_through_seq=?,state_digest=? "
            "WHERE projection_name='work'",
            (projection.frontier, projection_digest(projection)),
        )
        self._db.execute(
            "UPDATE p1_projection_state SET frontier_seq=?,head_digest=?,"
            "open_obligation_count=?,unresolved_finding_count=?,freshness=?,"
            "unknown_event_count=?,status_coverage_canonical=?,status_gap_codes_canonical=? "
            "WHERE projection_name='work'",
            (
                projection.frontier,
                projection.head_digest,
                len(projection.obligations),
                len(projection.findings),
                projection.freshness.value,
                projection.unknown_event_count,
                canonical_encode(coverage_to_json(records[-1].coverage)),
                canonical_encode(projection.coverage_gaps),
            ),
        )

    async def append_batch(self, command: AppendCommand) -> AppendResult:
        await self._ensure_recovered()
        async with self._lock:
            durable_writer = self._db.execute(
                "SELECT next_writer_seq,head_entry_digest FROM writers WHERE writer_id=?",
                (command.writer_id,),
            ).fetchone()
            cached_writer = self._state.writers.get(command.writer_id)
            if durable_writer is not None and (
                cached_writer is None
                or durable_writer != (cached_writer.next_sequence, cached_writer.head_digest)
            ):
                raise _public_error(PublicErrorCode.EVENT_INVALID)
            existing = _operation_digest_row(self._db, command.writer_id, command.operation_id)
            if existing is not None:
                digest, state = existing
                if digest != command.request_digest:
                    raise _public_error(PublicErrorCode.IDEMPOTENCY_CONFLICT)
                cached = self._state.operations.get((command.writer_id, command.operation_id))
                if state == "complete" and cached is not None and cached[1] is not None:
                    return replace(cached[1], outcome="replayed")
                raise _public_error(PublicErrorCode.STORAGE_CORRUPT)

            importer = any(
                row.publication_channel.value == "codex_jsonl_import" for row in command.entries
            )
            reservation: _Reservation | None = None
            if importer:
                reservation = _import_reservation(self._db, command)
            if command.operation_kind.value == "receipt":
                pending = self._db.execute(
                    "SELECT 1 FROM import_jobs WHERE session_id=? AND state='pending' LIMIT 1",
                    (command.session_id,),
                ).fetchone()
                if pending is not None:
                    raise _public_error(PublicErrorCode.OPERATION_PENDING, retryable=True)

            clone = MemoryLedgerState(
                records=self._state.records,
                operations=dict(self._state.operations),
                writers=dict(self._state.writers),
                projection=self._state.projection,
                frozen_cases=dict(self._state.frozen_cases),
                check_results=dict(self._state.check_results),
                check_errors=dict(self._state.check_errors),
                phase_objects=dict(self._state.phase_objects),
                jobs=dict(self._state.jobs),
                job_by_case=dict(self._state.job_by_case),
                attempts=dict(self._state.attempts),
                object_refs=dict(self._state.object_refs),
            )
            shim = _SqliteImportShim()
            shim.reservation = reservation
            oracle = MemoryLedgerAdapter(
                task_id=self._task_id,
                ownership_fence=self._fence,
                state=clone,
                import_state=shim,
                transaction_lock=asyncio.Lock(),
                clock=self._clock,
                ids=self._ids,
                objects=self._objects,
            )
            result = await oracle.append_batch(command)
            new_records = clone.records[len(self._state.records) :]
            try:
                self._db.execute("BEGIN IMMEDIATE")
                if (
                    _operation_digest_row(self._db, command.writer_id, command.operation_id)
                    is not None
                ):
                    raise _public_error(PublicErrorCode.IDEMPOTENCY_CONFLICT)
                if importer:
                    _import_reservation(self._db, command)
                if (
                    command.operation_kind.value == "receipt"
                    and self._db.execute(
                        "SELECT 1 FROM import_jobs WHERE session_id=? AND state='pending' LIMIT 1",
                        (command.session_id,),
                    ).fetchone()
                    is not None
                ):
                    raise _public_error(PublicErrorCode.OPERATION_PENDING, retryable=True)
                old_state = self._state
                self._state = clone
                try:
                    self._persist_append(command, result, new_records)
                except BaseException:
                    self._state = old_state
                    raise
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.get_autocommit() is False:
                    self._db.execute("ROLLBACK")
                raise
            return result

    async def _load_events_recovered(
        self, session_id: str, *, after: int, through: int | None
    ) -> AsyncIterator[LedgerRecord]:
        await self._ensure_recovered()
        async for record in self._oracle().load_events(session_id, after=after, through=through):
            yield record

    def load_events(
        self, session_id: str, *, after: int = 0, through: int | None = None
    ) -> AsyncIterator[LedgerRecord]:
        return self._load_events_recovered(session_id, after=after, through=through)

    def _oracle(self) -> MemoryLedgerAdapter:
        return MemoryLedgerAdapter(
            task_id=self._task_id,
            ownership_fence=self._fence,
            state=self._state,
            import_state=_SqliteImportShim(),
            transaction_lock=self._lock,
            clock=self._clock,
            ids=self._ids,
            objects=self._objects,
        )

    async def _sync_after_mutation(self, new_records: tuple[LedgerRecord, ...] = ()) -> None:
        async with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("PRAGMA defer_foreign_keys=ON")
                self._verify_owner()
                self._persist_derived_records(new_records)
                self._sync_runtime_state()
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.get_autocommit() is False:
                    self._db.execute("ROLLBACK")
                raise

    async def load_projection(
        self, session_id: str, view: ProjectionView
    ) -> StoredProjection | None:
        await self._ensure_recovered()
        return await self._oracle().load_projection(session_id, view)

    async def load_case_availability(
        self, session_id: str, frontier: Frontier, projection: ProjectionState
    ) -> CaseAvailabilityFacts:
        await self._ensure_recovered()
        return await self._oracle().load_case_availability(session_id, frontier, projection)

    async def query_projection(self, query: ProjectionQuery) -> ProjectionPage:
        await self._ensure_recovered()
        return await self._oracle().query_projection(query)

    async def lookup_operation(self, writer_id: str, operation_id: str) -> OperationRecord | None:
        await self._ensure_recovered()
        return await self._oracle().lookup_operation(writer_id, operation_id)

    async def reclaim_operation(
        self, writer_id: str, operation_id: str, request_digest: str
    ) -> OperationLease | PendingVerdict:
        await self._ensure_recovered()
        result = await self._oracle().reclaim_operation(writer_id, operation_id, request_digest)
        if type(result) is OperationLease:
            await self._sync_after_mutation()
        return result

    async def freeze_case(
        self,
        session_id: str,
        writer_id: str,
        expected_frontier: int | None,
        request_id: str,
        request_digest: str,
    ) -> FrozenCase | CheckCommitResult:
        await self._ensure_recovered()
        result = await self._oracle().freeze_case(
            session_id, writer_id, expected_frontier, request_id, request_digest
        )
        if type(result) is FrozenCase:
            await self._sync_after_mutation()
        return result

    async def advance_check_phase(
        self,
        lease: OperationLease,
        expected_phase: CheckPhase,
        next_phase: CheckPhase,
        durable_object_ref: ObjectRef | None = None,
    ) -> OperationLease:
        await self._ensure_recovered()
        result = await self._oracle().advance_check_phase(
            lease, expected_phase, next_phase, durable_object_ref
        )
        await self._sync_after_mutation()
        return result

    async def enqueue_semantic_job(
        self, lease: OperationLease, case_digest: str, case_object_ref: ObjectRef
    ) -> SemanticJobRecord:
        await self._ensure_recovered()
        result = await self._oracle().enqueue_semantic_job(lease, case_digest, case_object_ref)
        await self._sync_after_mutation()
        return result

    async def claim_semantic_job(self, lease: OperationLease, job_id: str) -> SemanticAttemptHandle:
        await self._ensure_recovered()
        result = await self._oracle().claim_semantic_job(lease, job_id)
        await self._sync_after_mutation()
        return result

    async def record_attempt_outcome(
        self,
        handle: SemanticAttemptHandle,
        outcome: AttemptOutcome,
        result_object_ref: ObjectRef | None = None,
        terminal_code: SemanticReason | None = None,
    ) -> None:
        await self._ensure_recovered()
        await self._oracle().record_attempt_outcome(
            handle, outcome, result_object_ref, terminal_code
        )
        await self._sync_after_mutation()

    async def select_attempt(
        self,
        lease: OperationLease,
        handle: SemanticAttemptHandle,
        selected_result_object_ref: ObjectRef,
    ) -> SelectedAttempt:
        await self._ensure_recovered()
        result = await self._oracle().select_attempt(lease, handle, selected_result_object_ref)
        await self._sync_after_mutation()
        return result

    async def renew_leases(self, lease: OperationLease) -> OperationLease:
        await self._ensure_recovered()
        result = await self._oracle().renew_leases(lease)
        await self._sync_after_mutation()
        return result

    async def commit_check_if_current(
        self,
        frozen: FrozenCase,
        findings: RankedFindings,
        policy_executions: tuple[CheckPolicyExecution, ...],
        semantic_status: SemanticStatus,
        semantic_reason: SemanticReason,
        semantic_provenance: SemanticProvenance | None,
        request_id: str,
    ) -> CheckCommitResult:
        await self._ensure_recovered()
        before = len(self._state.records)
        try:
            result = await self._oracle().commit_check_if_current(
                frozen,
                findings,
                policy_executions,
                semantic_status,
                semantic_reason,
                semantic_provenance,
                request_id,
            )
        except PublicOperationError:
            await self._sync_after_mutation()
            raise
        await self._sync_after_mutation(self._state.records[before:])
        return result

    async def run_passive_checkpoint(self, wal_page_threshold: int) -> CheckpointReport:
        await self._ensure_recovered()
        if type(wal_page_threshold) is not int or wal_page_threshold < 1:
            raise _public_error(PublicErrorCode.INVALID_REQUEST)
        async with self._lock:
            self._verify_owner()
            journal_mode = self._db.pragma("journal_mode")
            filename = self._db.filename
            if journal_mode != "wal" or not filename:
                return CheckpointReport(0, 0, 0)
            page_size = self._db.pragma("page_size")
            if type(page_size) is not int or page_size < 1:
                raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
            wal_path = Path(f"{filename}-wal")
            try:
                wal_bytes = wal_path.stat().st_size
            except FileNotFoundError:
                return CheckpointReport(0, 0, 0)
            wal_pages = (wal_bytes + page_size - 1) // page_size
            if wal_pages < wal_page_threshold:
                return CheckpointReport(0, wal_pages, 0)
            row = self._db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if row is None or len(row) != 3 or any(type(value) is not int for value in row):
            raise _public_error(PublicErrorCode.STORAGE_CORRUPT)
        return CheckpointReport(row[0], row[1], row[2])

    async def rebuild_projection(self, projection_name: str) -> None:
        await self._ensure_recovered()
        if projection_name != "work":
            raise _public_error(PublicErrorCode.INVALID_REQUEST)
        async with self._lock:
            projection = self._state.projection
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._verify_owner()
                self._db.execute(
                    "UPDATE projection_state SET projection_version=?,applied_through_seq=?,"
                    "state_digest=? WHERE projection_name='work'",
                    (PROJECTION_VERSION, projection.frontier, projection_digest(projection)),
                )
                self._db.execute("COMMIT")
            except BaseException:
                if self._db.get_autocommit() is False:
                    self._db.execute("ROLLBACK")
                raise
