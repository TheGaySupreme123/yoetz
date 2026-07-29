"""Memory/SQLite parity for the authoritative append and replay contract."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import apsw
import pytest

from builders.replay import replay_records
from yoetz.adapters.memory.importer import MemoryImportState
from yoetz.adapters.memory.ledger import MemoryLedgerAdapter, MemoryLedgerState
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.domain.events import EventDraft, EventPayload, UnknownEvent
from yoetz.domain.findings import CheckVerdict, RankedFindings
from yoetz.domain.values import parse_rfc3339_millis
from yoetz.ports.ledger import (
    AppendCommand,
    AppendEntry,
    AppendWarning,
    AttemptOutcome,
    CheckCommitResult,
    CheckPhase,
    CheckPolicyExecution,
    FrozenCase,
    OperationKind,
    SelectedAttempt,
)
from yoetz.ports.objects import (
    ObjectKind,
    ObjectMetadata,
    ObjectRef,
    ObjectRootSnapshot,
    ObjectSource,
    StagedObject,
)
from yoetz.ports.runtime import OwnershipFence
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind
from yoetz.protocol.models import SemanticReason, SemanticStatus


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Clock:
    def now_utc(self) -> datetime:
        return datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

    def monotonic_seconds(self) -> float:
        return 1.0


class _Ids:
    def __init__(self) -> None:
        self._next = 100

    def new(self, kind: IdKind) -> str:
        self._next += 1
        return PREFIX_BY_KIND[kind] + str(uuid.UUID(int=self._next, version=4))


class _Objects:
    def __init__(self, ids: _Ids) -> None:
        self._ids = ids
        self._data: dict[str, bytes] = {}
        self._refs: dict[str, ObjectRef] = {}

    async def commitment_for(self, data: bytes, kind: ObjectKind) -> str:
        del kind
        return "hmac-sha256:" + hashlib.sha256(data).hexdigest()

    async def stage(self, source: ObjectSource, metadata: ObjectMetadata) -> StagedObject:
        assert source.data is not None
        object_id = self._ids.new(IdKind.OBJECT)
        commitment = await self.commitment_for(source.data, metadata.kind)
        self._data[object_id] = source.data
        return StagedObject(
            object_id,
            len(source.data),
            commitment,
            "sha256:" + hashlib.sha256(b"envelope" + source.data).hexdigest(),
            "yoetz-object/1",
            "slot-1",
            metadata,
            object(),
        )

    async def finalize(self, staged: StagedObject) -> ObjectRef:
        ref = ObjectRef(
            staged.object_id,
            staged.plaintext_size,
            staged.commitment,
            staged.envelope_digest,
            staged.encryption_format,
            staged.key_slot,
            staged.metadata,
        )
        self._refs[ref.object_id] = ref
        return ref

    async def resolve_verified(self, object_id: str, envelope_digest: str) -> ObjectRef:
        ref = self._refs[object_id]
        if ref.envelope_digest != envelope_digest:
            raise ValueError("object_verification_failed")
        return ref

    async def _open(self, ref: ObjectRef) -> AsyncIterator[bytes]:
        yield self._data[ref.object_id]

    def open_verified(self, ref: ObjectRef) -> AsyncIterator[bytes]:
        return self._open(ref)

    async def sweep_orphans(self, root_snapshot: ObjectRootSnapshot, now: datetime) -> int:
        del root_snapshot, now
        return 0


def _fence() -> OwnershipFence:
    return OwnershipFence(
        "svc_00000000-0000-4000-8000-000000000001",
        1,
        1,
        "ledger-test-nonce",
    )


def ledger_command(*, request_suffix: str = "1", unknown: bool = False) -> AppendCommand:
    vector = "unknown-schema" if unknown else "projection-rebuild"
    records = replay_records(vector)
    record = next(row for row in records if type(row) is UnknownEvent) if unknown else records[0]
    assert record.payload is not None
    operation_id = f"req_00000000-0000-4000-8000-00000000000{request_suffix}"
    draft = EventDraft(
        event_id=record.event_id,
        schema=record.schema,
        occurred_at=record.occurred_at,
        causal_parents=() if unknown else record.causal_parents,
        payload=cast(EventPayload, record.payload) if not unknown else record.payload,
        artifact_refs=record.artifact_refs,
        evidence_refs=record.evidence_refs,
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
    entry = AppendEntry(
        draft,
        record.author,
        ref,
        ref.commitment,
        metadata.media_type,
        ref.plaintext_size,
        record.publication_channel,
        record.coverage,
        "unknown_unprojected" if unknown else "projected",
    )
    return AppendCommand(
        record.task_id,
        record.session_id,
        record.writer.writer_id,
        operation_id,
        OperationKind.PUBLISH_WORK,
        "sha256:" + "2" * 64,
        0,
        (entry,),
    )


def memory_ledger(command: AppendCommand) -> MemoryLedgerAdapter:
    ids = _Ids()
    return MemoryLedgerAdapter(
        task_id=command.task_id,
        ownership_fence=_fence(),
        state=MemoryLedgerState(),
        import_state=MemoryImportState(),
        transaction_lock=asyncio.Lock(),
        clock=_Clock(),
        ids=ids,
        objects=_Objects(ids),
    )


def sqlite_ledger(command: AppendCommand) -> SqliteLedger:
    db = apsw.Connection(":memory:")
    initialize_bundle(
        db,
        {
            "task_id": command.task_id,
            "owner_generation": "1",
            "owner_nonce": "ledger-test-nonce",
        },
    )
    ids = _Ids()
    return SqliteLedger(
        db=db,
        task_id=command.task_id,
        ownership_fence=_fence(),
        clock=_Clock(),
        ids=ids,
        objects=_Objects(ids),
    )


@pytest.mark.anyio
async def test_append_batch_contract() -> None:
    command = ledger_command()
    memory = memory_ledger(command)
    sqlite = sqlite_ledger(command)
    expected = await memory.append_batch(command)
    actual = await sqlite.append_batch(command)
    assert actual == expected
    assert (await memory.append_batch(command)).outcome == "replayed"
    assert (await sqlite.append_batch(command)).outcome == "replayed"

    conflict = replace(command, request_digest="sha256:" + "3" * 64)
    for adapter in (memory, sqlite):
        with pytest.raises(PublicOperationError) as caught:
            await adapter.append_batch(conflict)
        assert caught.value.code is PublicErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.anyio
async def test_load_and_freeze_contract() -> None:
    command = ledger_command()
    adapters = (memory_ledger(command), sqlite_ledger(command))
    loaded: list[tuple[object, ...]] = []
    for adapter in adapters:
        await adapter.append_batch(command)
        loaded.append(tuple([row async for row in adapter.load_events(command.session_id)]))
    assert loaded[0] == loaded[1]


@pytest.mark.anyio
async def test_append_warning_contract() -> None:
    command = ledger_command(request_suffix="2", unknown=True)
    results = [
        await adapter.append_batch(command)
        for adapter in (memory_ledger(command), sqlite_ledger(command))
    ]
    assert results[0] == results[1]
    assert results[0].warnings == (AppendWarning.UNKNOWN_EVENT_SCHEMA_PRESERVED,)


@pytest.mark.anyio
async def test_lookup_operation_contract() -> None:
    command = ledger_command()
    for adapter in (memory_ledger(command), sqlite_ledger(command)):
        result = await adapter.append_batch(command)
        operation = await adapter.lookup_operation(command.writer_id, command.operation_id)
        assert operation is not None
        assert operation.request_digest == command.request_digest
        assert operation.result_locator is not None
        assert (
            operation.result_locator.first_ingestion_sequence
            == result.accepted[0].ingestion_sequence
        )


async def _local_result_ref(
    adapter: MemoryLedgerAdapter | SqliteLedger, command: AppendCommand
) -> ObjectRef:
    objects = adapter._objects  # pyright: ignore[reportPrivateUsage]
    assert objects is not None
    staged = await objects.stage(
        ObjectSource(data=b"{}", declared_size=2),
        ObjectMetadata(
            ObjectKind.DETERMINISTIC_RESULT,
            "application/vnd.yoetz.deterministic-result+json",
            command.task_id,
            datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        ),
    )
    return await objects.finalize(staged)


async def _object_ref(
    adapter: MemoryLedgerAdapter | SqliteLedger,
    command: AppendCommand,
    kind: ObjectKind,
) -> ObjectRef:
    objects = adapter._objects  # pyright: ignore[reportPrivateUsage]
    assert objects is not None
    staged = await objects.stage(
        ObjectSource(data=b"{}", declared_size=2),
        ObjectMetadata(
            kind,
            "application/json",
            command.task_id,
            datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        ),
    )
    return await objects.finalize(staged)


@pytest.mark.anyio
async def test_commit_check_if_current_contract() -> None:
    command = ledger_command()
    results: list[CheckCommitResult] = []
    for adapter in (memory_ledger(command), sqlite_ledger(command)):
        await adapter.append_batch(command)
        frozen = await adapter.freeze_case(
            command.session_id,
            command.writer_id,
            1,
            "req_00000000-0000-4000-8000-000000000009",
            "sha256:" + "9" * 64,
        )
        assert type(frozen) is FrozenCase
        lease = await adapter.advance_check_phase(
            frozen.lease,
            CheckPhase.RESERVED,
            CheckPhase.LOCAL_READY,
            await _local_result_ref(adapter, command),
        )
        lease = await adapter.advance_check_phase(
            lease,
            CheckPhase.LOCAL_READY,
            CheckPhase.READY_TO_FINALIZE,
        )
        ranked = RankedFindings((), 0, CheckVerdict.NO_ISSUE_DETECTED, command.entries[0].coverage)
        result = await adapter.commit_check_if_current(
            FrozenCase(frozen.case, lease),
            ranked,
            (CheckPolicyExecution("research-evidence", "0.1.0", "run", "completed"),),
            SemanticStatus.NOT_REQUESTED,
            SemanticReason.DETERMINISTIC_MODE,
            None,
            lease.operation_id,
        )
        assert result.outcome == "committed"
        results.append(result)
    assert results[0] == results[1]


@pytest.mark.anyio
async def test_semantic_attempt_selection_contract() -> None:
    command = ledger_command()
    selected: list[SelectedAttempt] = []
    for adapter in (memory_ledger(command), sqlite_ledger(command)):
        await adapter.append_batch(command)
        frozen = await adapter.freeze_case(
            command.session_id,
            command.writer_id,
            1,
            "req_00000000-0000-4000-8000-000000000008",
            "sha256:" + "8" * 64,
        )
        assert type(frozen) is FrozenCase
        lease = await adapter.advance_check_phase(
            frozen.lease,
            CheckPhase.RESERVED,
            CheckPhase.LOCAL_READY,
            await _local_result_ref(adapter, command),
        )
        lease = await adapter.advance_check_phase(
            lease,
            CheckPhase.LOCAL_READY,
            CheckPhase.SEMANTIC_WAIT,
        )
        case_ref = await _object_ref(adapter, command, ObjectKind.SEMANTIC_CASE)
        job = await adapter.enqueue_semantic_job(lease, "sha256:" + "7" * 64, case_ref)
        handle = await adapter.claim_semantic_job(lease, job.job_id)
        response_ref = await _object_ref(adapter, command, ObjectKind.SEMANTIC_RESPONSE)
        await adapter.record_attempt_outcome(handle, AttemptOutcome.RESPONSE_DURABLE, response_ref)
        selected.append(await adapter.select_attempt(lease, handle, response_ref))
        loaded = await adapter.load_semantic_job(command.writer_id, frozen.lease.operation_id)
        assert loaded is not None
        assert loaded.selected_attempt_id == selected[-1].attempt_id
        attempts = await adapter.list_semantic_attempts(loaded.job_id)
        assert len(attempts) == 1
        assert attempts[0].state == "selected"
        assert attempts[0].attempt_id == selected[-1].attempt_id
    assert selected[0] == selected[1]


@pytest.mark.anyio
async def test_semantic_claim_resumes_same_started_attempt_for_owner() -> None:
    """Crash before authorization consumption resumes the same attempt identity."""

    command = ledger_command()
    for adapter in (memory_ledger(command), sqlite_ledger(command)):
        await adapter.append_batch(command)
        frozen = await adapter.freeze_case(
            command.session_id,
            command.writer_id,
            1,
            "req_00000000-0000-4000-8000-000000000018",
            "sha256:" + "8" * 64,
        )
        assert type(frozen) is FrozenCase
        lease = await adapter.advance_check_phase(
            frozen.lease,
            CheckPhase.RESERVED,
            CheckPhase.LOCAL_READY,
            await _local_result_ref(adapter, command),
        )
        lease = await adapter.advance_check_phase(
            lease,
            CheckPhase.LOCAL_READY,
            CheckPhase.SEMANTIC_WAIT,
        )
        case_ref = await _object_ref(adapter, command, ObjectKind.SEMANTIC_CASE)
        job = await adapter.enqueue_semantic_job(lease, "sha256:" + "7" * 64, case_ref)
        first = await adapter.claim_semantic_job(lease, job.job_id)
        second = await adapter.claim_semantic_job(lease, job.job_id)
        assert first.attempt_id == second.attempt_id
        assert first.provider_request_id == second.provider_request_id
        assert first.attempt_ordinal == 1
