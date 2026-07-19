"""Durable append, atomic rejection, and cache-independent replay acceptance."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import apsw
import pytest

from builders.ledger_adapters import (
    FixedClock,
    FixedIds,
    MemoryObjects,
    ownership_fence,
)
from builders.replay import replay_records
from yoetz.adapters.memory.importer import MemoryImportState
from yoetz.adapters.memory.ledger import MemoryLedgerAdapter, MemoryLedgerState
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.domain.events import EventDraft, LedgerRecord, UnknownEvent, encode_payload
from yoetz.domain.values import Frontier, event_id, object_id, parse_rfc3339_millis
from yoetz.kernel.projections import ProjectionState, projection_digest
from yoetz.kernel.reducers import replay
from yoetz.ports.ledger import AppendCommand, AppendEntry, OperationKind, ProjectionView
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def uuid_id(prefix: str, number: int) -> str:
    return f"{prefix}_{uuid.UUID(int=number, version=4)}"


def _entry(record: LedgerRecord, objects: MemoryObjects) -> AppendEntry:
    assert record.payload is not None
    known = not isinstance(record, UnknownEvent)
    draft = EventDraft(
        record.event_id,
        record.schema,
        record.occurred_at,
        record.causal_parents,
        record.payload,
        record.artifact_refs,
        record.evidence_refs,
    )
    metadata = ObjectMetadata(
        ObjectKind.EVENT_PAYLOAD,
        record.payload_ref.media_type,
        record.task_id,
        parse_rfc3339_millis(record.ledger.accepted_at.wire),
    )
    ref = ObjectRef(
        record.payload_ref.object_id,
        record.payload_ref.plaintext_size,
        record.payload_ref.commitment,
        "sha256:" + "1" * 64,
        "yoetz-object/1",
        "slot-1",
        metadata,
    )
    # The acceptance harness models the application guarantee that payload
    # objects are durable before ledger publication.
    object_bytes = canonical_encode(record.payload if not known else encode_payload(record.payload))
    objects._data[ref.object_id] = object_bytes  # pyright: ignore[reportPrivateUsage]
    return AppendEntry(
        draft,
        record.author,
        ref,
        ref.commitment,
        metadata.media_type,
        ref.plaintext_size,
        record.publication_channel,
        record.coverage,
        "projected" if known else "unknown_unprojected",
    )


def command_from_records(
    records: Sequence[LedgerRecord],
    *,
    request_number: int = 1,
    expected_frontier: int | None = 0,
    objects: MemoryObjects | None = None,
) -> tuple[AppendCommand, MemoryObjects]:
    if not records:
        raise ValueError("records_required")
    store = MemoryObjects(FixedIds()) if objects is None else objects
    first = records[0]
    operation_id = uuid_id("req", request_number)
    entries = tuple(_entry(record, store) for record in records)
    digest = canonical_digest(
        {
            "event_ids": tuple(entry.draft.event_id for entry in entries),
            "operation_id": operation_id,
        }
    )
    return (
        AppendCommand(
            first.task_id,
            first.session_id,
            first.writer.writer_id,
            operation_id,
            OperationKind.PUBLISH_WORK,
            digest,
            expected_frontier,
            entries,
        ),
        store,
    )


def batch_command(count: int, *, unknown: bool = False) -> tuple[AppendCommand, MemoryObjects]:
    template = (
        next(
            record
            for record in replay_records("unknown-schema")
            if isinstance(record, UnknownEvent)
        )
        if unknown
        else replay_records("projection-rebuild")[0]
    )
    objects = MemoryObjects(FixedIds())
    template_entry = _entry(template, objects)
    entries: list[AppendEntry] = []
    for ordinal in range(1, count + 1):
        draft = replace(
            template_entry.draft,
            event_id=event_id(uuid_id("evt", 10_000 + ordinal)),
            causal_parents=(),
        )
        ref = replace(
            template_entry.payload_object,
            object_id=object_id(uuid_id("obj", 20_000 + ordinal)),
        )
        objects._data[ref.object_id] = objects._data[  # pyright: ignore[reportPrivateUsage]
            template_entry.payload_object.object_id
        ]
        entries.append(
            replace(
                template_entry,
                draft=draft,
                payload_object=ref,
                payload_commitment=ref.commitment,
            )
        )
    operation_id = uuid_id("req", 30_000 + count)
    return (
        AppendCommand(
            template.task_id,
            template.session_id,
            template.writer.writer_id,
            operation_id,
            OperationKind.PUBLISH_WORK,
            canonical_digest(
                {
                    "event_ids": tuple(entry.draft.event_id for entry in entries),
                    "operation_id": operation_id,
                }
            ),
            0,
            tuple(entries),
        ),
        objects,
    )


def memory_for(command: AppendCommand, objects: MemoryObjects) -> MemoryLedgerAdapter:
    return MemoryLedgerAdapter(
        task_id=command.task_id,
        ownership_fence=ownership_fence(),
        state=MemoryLedgerState(),
        import_state=MemoryImportState(),
        transaction_lock=asyncio.Lock(),
        clock=FixedClock(),
        ids=FixedIds(),
        objects=objects,
    )


def sqlite_for(
    command: AppendCommand,
    objects: MemoryObjects,
    db: apsw.Connection,
) -> SqliteLedger:
    if db.execute("PRAGMA user_version").fetchone() == (0,):
        initialize_bundle(
            db,
            {
                "task_id": command.task_id,
                "owner_generation": "1",
                "owner_nonce": "ledger-test-nonce",
            },
        )
    return SqliteLedger(
        db=db,
        task_id=command.task_id,
        ownership_fence=ownership_fence(),
        clock=FixedClock(),
        ids=FixedIds(),
        objects=objects,
    )


def file_sqlite_for(
    command: AppendCommand,
    objects: MemoryObjects,
    path: Path,
) -> tuple[SqliteLedger, apsw.Connection]:
    db = apsw.Connection(str(path))
    return sqlite_for(command, objects, db), db


@pytest.mark.anyio
async def test_single_and_max_batch_boundaries(tmp_path: Path) -> None:
    for count in (1, 100):
        command, objects = batch_command(count)
        ledger, db = file_sqlite_for(command, objects, tmp_path / f"batch-{count}.sqlite3")
        result = await ledger.append_batch(command)
        loaded = tuple([row async for row in ledger.load_events(command.session_id)])
        assert len(result.accepted) == count
        assert tuple(item.ingestion_sequence for item in result.accepted) == tuple(
            range(1, count + 1)
        )
        assert len(loaded) == count
        assert result.result_frontier.sequence == count
        assert db.execute("SELECT count(*) FROM events").fetchone() == (count,)
        db.close()

    opaque, opaque_objects = batch_command(1, unknown=True)
    opaque_ledger = sqlite_for(opaque, opaque_objects, apsw.Connection(":memory:"))
    opaque_result = await opaque_ledger.append_batch(opaque)
    opaque_rows = tuple([row async for row in opaque_ledger.load_events(opaque.session_id)])
    assert isinstance(opaque_rows[0], UnknownEvent)
    assert opaque_result.warnings[0].value == "unknown_event_schema_preserved"


@pytest.mark.anyio
async def test_same_and_equivalent_retry_is_stable() -> None:
    records = replay_records("projection-rebuild")[:1]
    command, objects = command_from_records(records)
    ledger = sqlite_for(command, objects, apsw.Connection(":memory:"))
    accepted = await ledger.append_batch(command)
    same = await ledger.append_batch(command)
    equivalent = replace(
        command,
        entries=tuple(replace(entry, draft=replace(entry.draft)) for entry in command.entries),
    )
    retried = await ledger.append_batch(equivalent)
    assert same == retried == replace(accepted, outcome="replayed")
    operation = await ledger.lookup_operation(command.writer_id, command.operation_id)
    assert operation is not None
    assert operation.result_canonical is not None
    assert (
        operation.result_digest
        == "sha256:" + hashlib.sha256(operation.result_canonical).hexdigest()
    )


@pytest.mark.anyio
async def test_reused_request_with_changed_identity_conflicts() -> None:
    command, objects = command_from_records(replay_records("projection-rebuild")[:1])
    ledger = sqlite_for(command, objects, apsw.Connection(":memory:"))
    await ledger.append_batch(command)
    changed = replace(command, request_digest="sha256:" + "f" * 64)
    with pytest.raises(PublicOperationError) as caught:
        await ledger.append_batch(changed)
    assert caught.value.code is PublicErrorCode.IDEMPOTENCY_CONFLICT
    assert tuple([row async for row in ledger.load_events(command.session_id)])


@pytest.mark.anyio
async def test_wrong_sequence_predecessor_and_invalid_known_event_reject_batch() -> None:
    first, first_objects = command_from_records(replay_records("projection-rebuild")[:1])
    db = apsw.Connection(":memory:")
    ledger = sqlite_for(first, first_objects, db)
    await ledger.append_batch(first)
    db.execute(
        "UPDATE writers SET head_entry_digest=? WHERE writer_id=?",
        ("sha256:" + "e" * 64, first.writer_id),
    )
    second_entry = replace(
        first.entries[0],
        draft=replace(first.entries[0].draft, event_id=event_id(uuid_id("evt", 2))),
        payload_object=replace(
            first.entries[0].payload_object,
            object_id=object_id(uuid_id("obj", 2)),
        ),
    )
    second = replace(
        first,
        operation_id=uuid_id("req", 2),
        request_digest="sha256:" + "d" * 64,
        expected_frontier=1,
        entries=(second_entry,),
    )
    first_objects._data[  # pyright: ignore[reportPrivateUsage]
        second_entry.payload_object.object_id
    ] = b"second-payload"
    with pytest.raises(PublicOperationError) as predecessor_error:
        await ledger.append_batch(second)
    assert predecessor_error.value.code is PublicErrorCode.EVENT_INVALID
    assert db.execute("SELECT count(*) FROM events").fetchone() == (1,)
    assert db.execute(
        "SELECT count(*) FROM operations WHERE operation_id=?", (second.operation_id,)
    ).fetchone() == (0,)

    invalid, invalid_objects = command_from_records(replay_records("projection-rebuild")[:1])
    invalid_entry = replace(
        invalid.entries[0],
        draft=replace(
            invalid.entries[0].draft,
            causal_parents=(event_id(uuid_id("evt", 999_999)),),
        ),
    )
    invalid = replace(invalid, entries=(invalid_entry,))
    invalid_db = apsw.Connection(":memory:")
    invalid_ledger = sqlite_for(invalid, invalid_objects, invalid_db)
    with pytest.raises(PublicOperationError) as event_error:
        await invalid_ledger.append_batch(invalid)
    assert event_error.value.code is PublicErrorCode.EVENT_INVALID
    assert invalid_db.execute("SELECT count(*) FROM events").fetchone() == (0,)
    assert invalid_db.execute("SELECT count(*) FROM operations").fetchone() == (0,)


@pytest.mark.anyio
async def test_replay_after_append_matches_reference_projection(tmp_path: Path) -> None:
    records = replay_records("all-event-families")
    command, objects = command_from_records(records)
    memory = memory_for(command, objects)
    memory_result = await memory.append_batch(command)
    expected_records = tuple([row async for row in memory.load_events(command.session_id)])
    expected = replay(expected_records)
    path = tmp_path / "reopen.sqlite3"
    sqlite, db = file_sqlite_for(command, objects, path)
    sqlite_result = await sqlite.append_batch(command)
    assert sqlite_result == memory_result
    db.close()

    reopened, reopened_db = file_sqlite_for(command, objects, path)
    durable = tuple([row async for row in reopened.load_events(command.session_id)])
    assert len(durable) == len(records)
    assert durable == expected_records
    assert projection_digest(replay(durable)) == projection_digest(expected)
    stored = await reopened.load_projection(command.session_id, ProjectionView.CANDIDATE_FINDINGS)
    assert stored is not None
    assert type(stored.state) is ProjectionState
    assert projection_digest(stored.state) == projection_digest(expected)
    operation = await reopened.lookup_operation(command.writer_id, command.operation_id)
    assert operation is not None
    assert (await reopened.append_batch(command)).outcome == "replayed"
    assert operation.result_locator is not None
    assert operation.result_locator.last_ingestion_sequence == len(records)
    assert stored.frontier == Frontier(expected.frontier, expected.head_digest)
    reopened_db.close()
