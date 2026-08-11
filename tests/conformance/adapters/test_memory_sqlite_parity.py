"""Public-artifact parity for memory and durable SQLite ledgers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import apsw
import pytest

from builders.ledger_adapters import MemoryObjects
from builders.replay import replay_records
from integration.storage.test_append_and_replay import (
    command_from_records,
    file_sqlite_for,
    memory_for,
    sqlite_for,
    uuid_id,
)
from yoetz.domain.events import (
    CheckRecordedPayload,
    ClaimRecordedPayload,
    EvidenceRecordedPayload,
    FindingRecordedPayload,
)
from yoetz.domain.findings import CheckVerdict, Finding, RankedFindings
from yoetz.domain.values import Actor, ActorType, Frontier, actor_id, finding_id
from yoetz.kernel.deterministic_checks import (
    CaseAvailabilityFacts,
    UnavailableCapturedObject,
)
from yoetz.kernel.projections import ProjectionState
from yoetz.ports.ledger import (
    AppendCommand,
    CheckCommitResult,
    CheckPhase,
    CheckPolicyExecution,
    FrozenCase,
    LedgerPort,
    ProjectionPage,
    ProjectionPosition,
    ProjectionQuery,
    ProjectionView,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectSource
from yoetz.protocol.coverage import (
    AuthorshipAssurance,
    PublicationChannel,
    coverage_for_channel,
)
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    SemanticReason,
    SemanticStatus,
    StatusFindingItemModel,
    StatusHistoryItemModel,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _query_all(
    ledger: LedgerPort, command: AppendCommand, frontier: Frontier
) -> dict[str, tuple[ProjectionPage, ...]]:
    result: dict[str, tuple[ProjectionPage, ...]] = {}
    for view in (
        "compact",
        "assignment",
        "obligations",
        "findings",
        "evidence",
        "history",
        "versions",
    ):
        pages: list[ProjectionPage] = []
        position: ProjectionPosition | None = None
        expected_version: str | None = None
        while True:
            query = ProjectionQuery(
                command.session_id,
                view,
                None,
                frontier,
                2,
                position,
                expected_version,
            )
            page = await ledger.query_projection(query)
            pages.append(page)
            if page.next_position is None:
                break
            position = page.next_position
            expected_version = page.projection_version
        result[view] = tuple(pages)
    return result


@pytest.mark.anyio
async def test_publish_replay_precedes_stale_frontier_after_sqlite_reopen(
    tmp_path: Path,
) -> None:
    records = replay_records("projection-rebuild")[:1]
    command, objects = command_from_records(records, expected_frontier=0)
    memory = memory_for(command, objects)
    sqlite, db = file_sqlite_for(command, objects, tmp_path / "publish-replay.sqlite3")

    first_memory = await memory.append_batch(command)
    first_sqlite = await sqlite.append_batch(command)
    assert first_sqlite == first_memory
    db.close()

    reopened, reopened_db = file_sqlite_for(command, objects, tmp_path / "publish-replay.sqlite3")
    try:
        assert (await memory.append_batch(command)).outcome == "replayed"
        assert (await reopened.append_batch(command)).outcome == "replayed"

        memory_events = tuple([event async for event in memory.load_events(command.session_id)])
        sqlite_events = tuple([event async for event in reopened.load_events(command.session_id)])
        assert sqlite_events == memory_events
        assert tuple(event.event_id for event in sqlite_events) == tuple(
            event.event_id for event in records
        )

        frontier = first_sqlite.result_frontier
        memory_views = await _query_all(memory, command, frontier)
        sqlite_views = await _query_all(reopened, command, frontier)
        assert sqlite_views["compact"] == memory_views["compact"]
        assert sqlite_views["history"] == memory_views["history"]
        history_items = tuple(
            cast(StatusHistoryItemModel, item)
            for page in sqlite_views["history"]
            for item in page.items
        )
        history_ids = tuple(item.event_id for item in history_items)
        assert history_ids == tuple(event.event_id for event in records)
    finally:
        reopened_db.close()


async def _local_result_ref(objects: MemoryObjects, command: AppendCommand) -> ObjectRef:
    staged = await objects.stage(
        ObjectSource(data=b"{}", declared_size=2),
        ObjectMetadata(
            ObjectKind.DETERMINISTIC_RESULT,
            "application/vnd.yoetz.deterministic-result+json",
            command.task_id,
            command.entries[0].payload_object.metadata.created_at,
        ),
    )
    return await objects.finalize(staged)


def _committed_finding(command: AppendCommand, subject_frontier: Frontier) -> Finding:
    records = replay_records("all-event-families")
    template = next(
        record.payload for record in records if type(record.payload) is FindingRecordedPayload
    )
    return replace(
        template,
        finding_id=finding_id(uuid_id("fnd", 70_001)),
        subject_frontier=subject_frontier,
        coverage=command.entries[-1].coverage,
    )


async def _commit_one_check(
    ledger: LedgerPort,
    command: AppendCommand,
    objects: MemoryObjects,
    subject_frontier: Frontier,
) -> CheckCommitResult:
    check_request = uuid_id("req", 70_002)
    frozen = await ledger.freeze_case(
        command.session_id,
        command.writer_id,
        subject_frontier.sequence,
        check_request,
        "sha256:" + "7" * 64,
    )
    assert type(frozen) is FrozenCase
    lease = await ledger.advance_check_phase(
        frozen.lease,
        CheckPhase.RESERVED,
        CheckPhase.LOCAL_READY,
        await _local_result_ref(objects, command),
    )
    lease = await ledger.advance_check_phase(
        lease,
        CheckPhase.LOCAL_READY,
        CheckPhase.READY_TO_FINALIZE,
    )
    finding = _committed_finding(command, subject_frontier)
    ranked = RankedFindings(
        (finding,),
        3,
        CheckVerdict.ACTION_REQUIRED,
        command.entries[-1].coverage,
    )
    return await ledger.commit_check_if_current(
        FrozenCase(frozen.case, lease),
        ranked,
        (CheckPolicyExecution("work-integrity", "0.1.0", "run", "completed"),),
        SemanticStatus.NOT_REQUESTED,
        SemanticReason.DETERMINISTIC_MODE,
        None,
        check_request,
    )


@pytest.mark.anyio
async def test_sqlite_reopen_replays_reused_finding_payloads(tmp_path: Path) -> None:
    records = replay_records("all-event-families")[:11]
    command, objects = command_from_records(records)
    path = tmp_path / "reused-finding-replay.sqlite3"
    ledger, db = file_sqlite_for(command, objects, path)
    appended = await ledger.append_batch(command)
    finding = cast(Finding, records[9].payload)
    check_request = uuid_id("req", 70_003)
    request_digest = "sha256:" + "8" * 64
    frozen = await ledger.freeze_case(
        command.session_id,
        command.writer_id,
        appended.result_frontier.sequence,
        check_request,
        request_digest,
    )
    assert type(frozen) is FrozenCase
    lease = await ledger.advance_check_phase(
        frozen.lease,
        CheckPhase.RESERVED,
        CheckPhase.LOCAL_READY,
        await _local_result_ref(objects, command),
    )
    lease = await ledger.advance_check_phase(
        lease,
        CheckPhase.LOCAL_READY,
        CheckPhase.READY_TO_FINALIZE,
    )
    committed = await ledger.commit_check_if_current(
        FrozenCase(frozen.case, lease),
        RankedFindings((finding,), 0, CheckVerdict.ACTION_REQUIRED, finding.coverage),
        (CheckPolicyExecution("work-integrity", "0.1.0", "run", "completed"),),
        SemanticStatus.NOT_REQUESTED,
        SemanticReason.DETERMINISTIC_MODE,
        None,
        check_request,
    )
    assert committed.findings == (finding,)
    assert committed.result_frontier.sequence == appended.result_frontier.sequence + 1
    db.close()

    reopened, reopened_db = file_sqlite_for(command, objects, path)
    try:
        replayed = await reopened.freeze_case(
            command.session_id,
            command.writer_id,
            appended.result_frontier.sequence,
            check_request,
            request_digest,
        )
        assert type(replayed) is CheckCommitResult
        assert replayed == replace(committed, outcome="replayed")
        assert replayed.findings == (finding,)
    finally:
        reopened_db.close()


@pytest.mark.anyio
async def test_public_artifacts_match_across_backends(tmp_path: Path) -> None:
    records = replay_records("all-event-families")
    command, memory_objects = command_from_records(records)
    sqlite_command, sqlite_objects = command_from_records(records)
    assert sqlite_command == command
    memory = memory_for(command, memory_objects)
    sqlite, db = file_sqlite_for(sqlite_command, sqlite_objects, tmp_path / "parity.sqlite3")

    memory_append = await memory.append_batch(command)
    sqlite_append = await sqlite.append_batch(command)
    assert sqlite_append == memory_append
    memory_events = tuple([row async for row in memory.load_events(command.session_id)])
    sqlite_events = tuple([row async for row in sqlite.load_events(command.session_id)])
    assert sqlite_events == memory_events

    for view in ProjectionView:
        assert await sqlite.load_projection(
            command.session_id, view
        ) == await memory.load_projection(command.session_id, view)
    assert await _query_all(sqlite, command, sqlite_append.result_frontier) == await _query_all(
        memory, command, memory_append.result_frontier
    )

    check_memory = await _commit_one_check(
        memory, command, memory_objects, memory_append.result_frontier
    )
    check_sqlite = await _commit_one_check(
        sqlite, sqlite_command, sqlite_objects, sqlite_append.result_frontier
    )
    assert check_sqlite == check_memory
    assert check_sqlite.result_frontier.sequence == check_sqlite.subject_frontier.sequence + 2
    after = tuple([row async for row in sqlite.load_events(command.session_id)])
    assert tuple(row.schema.name for row in after[-2:]) == (
        "finding_recorded",
        "check_recorded",
    )
    assert after[-2].payload == check_sqlite.findings[0]
    assert type(after[-1].payload) is CheckRecordedPayload
    assert after[-1].payload.suppressed_count == 3

    await sqlite.rebuild_projection("work")
    rebuilt = await sqlite.load_projection(command.session_id, ProjectionView.CANDIDATE_FINDINGS)
    assert rebuilt is not None and type(rebuilt.state) is ProjectionState
    latest = rebuilt.state.latest_tested_state
    assert latest is not None
    assert latest.suppressed_count == 3
    assert latest.returned_finding_ids == (check_sqlite.findings[0].finding_id,)
    findings_page = await sqlite.query_projection(
        ProjectionQuery(
            command.session_id,
            "findings",
            None,
            check_sqlite.result_frontier,
            100,
            None,
            None,
        )
    )
    finding_items = tuple(
        item for item in findings_page.items if type(item) is StatusFindingItemModel
    )
    assert any(item.finding_id == check_sqlite.findings[0].finding_id for item in finding_items)
    db.close()


@pytest.mark.anyio
async def test_private_artifacts_are_not_compared(tmp_path: Path) -> None:
    records = replay_records("projection-rebuild")
    first_command, first_objects = command_from_records(records)
    second_command, second_objects = command_from_records(records)
    first, first_db = file_sqlite_for(first_command, first_objects, tmp_path / "private-a.sqlite3")
    second, second_db = file_sqlite_for(
        second_command, second_objects, tmp_path / "private-b.sqlite3"
    )
    first_result = await first.append_batch(first_command)
    second_result = await second.append_batch(second_command)
    assert first_result == second_result
    assert tuple([row async for row in first.load_events(first_command.session_id)]) == tuple(
        [row async for row in second.load_events(second_command.session_id)]
    )
    assert first_db.filename != second_db.filename
    first_db.execute("PRAGMA cache_size=-32")
    second_db.execute("PRAGMA cache_size=-128")
    assert first_db.pragma("cache_size") != second_db.pragma("cache_size")
    assert await _query_all(first, first_command, first_result.result_frontier) == await _query_all(
        second, second_command, second_result.result_frontier
    )
    assert str(tmp_path).encode() not in first_result.accepted[0].entry_digest.encode()
    first_db.close()
    second_db.close()


@pytest.mark.anyio
async def test_supported_failures_match_by_code_and_shape() -> None:
    command, objects = command_from_records(replay_records("projection-rebuild")[:1])
    memory = memory_for(command, objects)
    sqlite = sqlite_for(command, objects, apsw.Connection(":memory:"))
    await memory.append_batch(command)
    await sqlite.append_batch(command)

    mismatched = replace(command, request_digest="sha256:" + "9" * 64)
    failures: list[PublicOperationError] = []
    for ledger in (memory, sqlite):
        with pytest.raises(PublicOperationError) as caught:
            await ledger.append_batch(mismatched)
        failures.append(caught.value)
    assert failures[0].code is failures[1].code is PublicErrorCode.IDEMPOTENCY_CONFLICT
    assert failures[0].retryable == failures[1].retryable is False
    assert dict(failures[0].safe_details) == dict(failures[1].safe_details) == {}

    importer_entry = replace(
        command.entries[0],
        author=Actor(
            actor_id("importer.local"),
            ActorType.IMPORTER,
            AuthorshipAssurance.HARNESS_OBSERVED,
        ),
        publication_channel=PublicationChannel.CODEX_JSONL_IMPORT,
        coverage=coverage_for_channel(PublicationChannel.CODEX_JSONL_IMPORT),
    )
    importer = replace(
        command,
        operation_id=uuid_id("req", 80_001),
        request_digest="sha256:" + "8" * 64,
        expected_frontier=1,
        entries=(
            replace(
                importer_entry,
                draft=replace(
                    importer_entry.draft,
                    event_id=uuid_id("evt", 80_002),
                    causal_parents=(),
                ),
            ),
        ),
    )
    reservation_failures: list[PublicOperationError] = []
    for ledger in (memory, sqlite):
        with pytest.raises(PublicOperationError) as caught:
            await ledger.append_batch(importer)
        reservation_failures.append(caught.value)
    assert reservation_failures[0].code is reservation_failures[1].code
    assert reservation_failures[0].code is PublicErrorCode.STORAGE_CORRUPT
    assert dict(reservation_failures[0].safe_details) == dict(reservation_failures[1].safe_details)


@pytest.mark.anyio
async def test_case_availability_and_import_reservation_collisions(tmp_path: Path) -> None:
    records = replay_records("projection-rebuild")
    command, objects = command_from_records(records)
    memory = memory_for(command, objects)
    sqlite, db = file_sqlite_for(command, objects, tmp_path / "availability.sqlite3")
    memory_result = await memory.append_batch(command)
    sqlite_result = await sqlite.append_batch(command)
    assert sqlite_result == memory_result
    state = await memory.load_projection(command.session_id, ProjectionView.CANDIDATE_FINDINGS)
    assert state is not None and type(state.state) is ProjectionState

    missing_record = next(
        record for record in records if type(record.payload) is ClaimRecordedPayload
    )
    missing_entry = next(
        entry for entry in command.entries if entry.draft.event_id == missing_record.event_id
    )
    del objects._data[  # pyright: ignore[reportPrivateUsage]
        missing_entry.payload_object.object_id
    ]
    evidence_record = next(
        record for record in records if type(record.payload) is EvidenceRecordedPayload
    )
    evidence_payload = cast(EvidenceRecordedPayload, evidence_record.payload)
    assert evidence_payload.captured_object_id is not None
    expected = CaseAvailabilityFacts(
        (missing_record.event_id,),
        (
            UnavailableCapturedObject(
                evidence_record.event_id,
                evidence_payload.captured_object_id,
            ),
        ),
    )
    assert (
        await memory.load_case_availability(
            command.session_id, memory_result.result_frontier, state.state
        )
        == expected
    )
    assert (
        await sqlite.load_case_availability(
            command.session_id, sqlite_result.result_frontier, state.state
        )
        == expected
    )

    # The unique permanent source/ordinal reservation is the DB-enforced half
    # of W-C-001; a second request can never alias it.
    unique_indexes = tuple(
        row for row in db.execute("PRAGMA index_list(import_publication_requests)") if row[2] == 1
    )
    indexed_columns = {
        tuple(column[2] for column in db.execute(f"PRAGMA index_info('{index_row[1]}')"))
        for index_row in unique_indexes
    }
    assert ("source_identity_digest", "publication_ordinal") in indexed_columns
    assert ("publishing_writer_id", "request_id") in indexed_columns
    db.close()
