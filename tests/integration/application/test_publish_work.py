"""Integration coverage for application publication over the memory ledger oracle."""

from __future__ import annotations

from typing import cast

import pytest

from builders.ledger_adapters import (
    FixedClock,
    MemoryObjects,
    append_command,
    memory_adapter,
    ownership_fence,
)
from yoetz.application.publish_work import Application, execute_publish_work
from yoetz.application.status import Application as StatusApplication
from yoetz.application.status import execute_status
from yoetz.domain.values import session_id
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.objects import ObjectStorePort
from yoetz.ports.runtime import RouteCommand, TaskRuntime
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    PublishWorkDryRunModel,
    PublishWorkRequestModel,
    StatusOperationPageModel,
    StatusRequest,
)

pytestmark = pytest.mark.anyio


class _IdleImporter:
    async def status(self, session: str) -> ImportStatusSnapshot:
        return ImportStatusSnapshot(session_id(session), 0, 0, (), ())


class _Runtime:
    def __init__(self, task: TaskRuntime) -> None:
        self.task = task
        self.routes: list[RouteCommand] = []
        self.release_count = 0

    async def route(self, command: RouteCommand) -> TaskRuntime:
        self.routes.append(command)
        return self.task

    async def release(self, runtime: TaskRuntime) -> None:
        assert runtime is self.task
        self.release_count += 1


class _Application:
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime
        self.clock = FixedClock()
        self.status_cursor_key = b"publish-work-status-cursor-key!!"

    def authorizes_import_publication(self, request: PublishWorkRequestModel) -> bool:
        del request
        return False


def _composition() -> tuple[_Application, MemoryObjects]:
    seed = append_command()
    ledger = memory_adapter(seed)
    objects = cast(MemoryObjects, ledger._objects)  # pyright: ignore[reportPrivateUsage]
    assert objects is not None
    task = TaskRuntime(
        seed.task_id,
        seed.session_id,
        seed.writer_id,
        frozenset(
            {
                RuntimeCapability.WRITE,
                RuntimeCapability.STRUCTURAL_READ,
                RuntimeCapability.PAYLOAD_READ,
            }
        ),
        ledger,
        cast(ObjectStorePort, objects),
        cast(ImporterPort, _IdleImporter()),
        "0.1.0",
        "0.1.0",
        "0.1",
        "1.0.0",
        ownership_fence(),
    )
    runtime = _Runtime(task)
    return _Application(runtime), objects


def _draft(
    *,
    event_tail: int,
    action_tail: int,
    action_kind: str = "other",
) -> dict[str, object]:
    return {
        "event_id": f"evt_00000000-0000-4000-8000-{event_tail:012d}",
        "schema": {"name": "action_recorded", "version": "1.0.0"},
        "occurred_at": "2026-07-19T12:00:00.000Z",
        "causal_parents": (),
        "payload": {
            "action_id": f"act_00000000-0000-4000-8000-{action_tail:012d}",
            "action_kind": action_kind,
            "description": "Materialized one coherent slice",
        },
        "artifact_refs": (),
        "evidence_refs": (),
    }


def _request(
    *,
    family: str = "action_recorded",
    description: str = "Materialized one coherent slice",
    request_tail: int = 201,
    event_tail: int = 202,
    action_tail: int = 203,
    expected_frontier: object = None,
    event_drafts: tuple[dict[str, object], ...] | None = None,
) -> PublishWorkRequestModel:
    seed = append_command()
    payload: object
    if family == "action_recorded":
        payload = {
            "action_id": f"act_00000000-0000-4000-8000-{action_tail:012d}",
            "action_kind": "other",
            "description": description,
        }
    else:
        payload = {
            "task_title": "forbidden lifecycle publication",
            "client_kind": "test_client",
            "client_version": "0.1.0",
            "integration": "local_cli",
            "profile": "test-fake",
        }
    return PublishWorkRequestModel.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": f"req_00000000-0000-4000-8000-{request_tail:012d}",
            "session_id": seed.session_id,
            "writer_id": seed.writer_id,
            "expected_frontier": expected_frontier,
            "event_drafts": event_drafts
            or (
                {
                    "event_id": f"evt_00000000-0000-4000-8000-{event_tail:012d}",
                    "schema": {"name": family, "version": "1.0.0"},
                    "occurred_at": "2026-07-19T12:00:00.000Z",
                    "causal_parents": (),
                    "payload": payload,
                    "artifact_refs": (),
                    "evidence_refs": (),
                },
            ),
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "local_cli",
            },
        }
    )


async def test_rejected_draft_is_located_by_ordinal_without_echoing_payload_content() -> None:
    """A multi-draft batch must say which draft failed, using only frozen names and an ordinal.

    The 2026-07-26 dogfood published a four-event batch whose only diagnostic was
    ``reason_code: invalid_event_enum`` — enough to know something was wrong, not enough to know
    which event or field to fix.
    """

    app, _objects = _composition()
    secret = "never-echo-this-private-title"
    forbidden: dict[str, object] = {
        "event_id": "evt_00000000-0000-4000-8000-000000000305",
        "schema": {"name": "session_opened", "version": "1.0.0"},
        "occurred_at": "2026-07-19T12:00:00.000Z",
        "causal_parents": (),
        "payload": {
            "task_title": secret,
            "client_kind": "test_client",
            "client_version": "0.1.0",
            "integration": "local_cli",
            "profile": "test-fake",
        },
        "artifact_refs": (),
        "evidence_refs": (),
    }
    request = _request(
        event_drafts=(
            _draft(event_tail=301, action_tail=302),
            _draft(event_tail=303, action_tail=304),
            forbidden,
        )
    )

    with pytest.raises(PublicOperationError) as caught:
        await execute_publish_work(cast(Application, app), request)

    details = caught.value.safe_details
    # The ordinal is the failing draft, not merely the first one.
    assert details["field"] == "/event_drafts/2/schema"
    assert details["reason_code"] == "event_family_not_admitted"
    assert secret not in repr(dict(details))
    assert secret not in caught.value.message


async def test_publish_is_object_first_atomic_and_same_id_replay_is_stable() -> None:
    app, objects = _composition()
    first = await execute_publish_work(cast(Application, app), _request())
    durable_after_first = len(objects._data)  # pyright: ignore[reportPrivateUsage]
    replay = await execute_publish_work(cast(Application, app), _request())

    assert first.ok is True
    assert first.outcome == "accepted"
    assert replay.ok is True
    assert replay.outcome == "replayed"
    assert replay.accepted_events == first.accepted_events
    assert len(objects._data) == durable_after_first  # pyright: ignore[reportPrivateUsage]
    assert app.runtime.release_count == 2


async def test_forbidden_family_rejects_before_object_publication() -> None:
    app, objects = _composition()
    before = len(objects._data)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(PublicOperationError) as caught:
        await execute_publish_work(cast(Application, app), _request(family="session_opened"))

    assert caught.value.code is PublicErrorCode.EVENT_INVALID
    # The rejected draft is named by ordinal and owning field so a multi-draft batch does not
    # have to be re-derived to find the one bad member.
    assert caught.value.safe_details == {
        "reason_code": "event_family_not_admitted",
        "field": "/event_drafts/0/schema",
    }
    assert len(objects._data) == before  # pyright: ignore[reportPrivateUsage]
    assert app.runtime.release_count == 1


async def test_same_request_id_replay_returns_the_stored_result_without_rewriting() -> None:
    """The recovery the MCP post-commit error advertises must actually work.

    When response shaping fails after a durable commit, the bridge tells the caller to retry with
    the same request_id. That guidance is only honest if an identical replay returns the committed
    result instead of appending a second event.
    """

    app, objects = _composition()
    request = _request(expected_frontier={"sequence": "0", "head_digest": "genesis"})
    first = await execute_publish_work(cast(Application, app), request)
    durable_after_first = len(objects._data)  # pyright: ignore[reportPrivateUsage]

    # This exact frontier is stale after the first append. Completed-operation replay must resolve
    # before the ledger evaluates it, or this would incorrectly become FRONTIER_CONFLICT.
    second = await execute_publish_work(cast(Application, app), request)

    assert first.outcome == "accepted"
    assert second.outcome == "replayed"
    # The replayed response must describe the same committed events and frontier as the original.
    assert second.result_frontier == first.result_frontier
    assert second.subject_frontier == first.subject_frontier
    assert tuple(item.event_id for item in second.accepted_events) == tuple(
        item.event_id for item in first.accepted_events
    )
    assert tuple(item.ingestion_sequence for item in second.accepted_events) == tuple(
        item.ingestion_sequence for item in first.accepted_events
    )
    # No second write: the replay reads stored state rather than re-publishing.
    assert len(objects._data) == durable_after_first  # pyright: ignore[reportPrivateUsage]


async def test_same_id_changed_logical_request_conflicts_before_reencryption() -> None:
    app, objects = _composition()
    first = await execute_publish_work(cast(Application, app), _request())
    durable_after_first = len(objects._data)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(PublicOperationError) as caught:
        await execute_publish_work(
            cast(Application, app), _request(description="Changed logical work")
        )

    assert caught.value.code is PublicErrorCode.REQUEST_IDENTITY_CONFLICT
    assert caught.value.safe_details.get("reason_code") == "request_identity_conflict"
    assert caught.value.safe_details.get("sequence") == first.result_frontier.sequence
    assert caught.value.safe_details.get("head_digest") == first.result_frontier.head_digest
    assert caught.value.safe_details.get("count") == len(first.accepted_events)
    assert len(objects._data) == durable_after_first  # pyright: ignore[reportPrivateUsage]


async def test_stale_expected_frontier_sequence_conflicts() -> None:
    app, _ = _composition()
    first = await execute_publish_work(cast(Application, app), _request())

    with pytest.raises(PublicOperationError) as caught:
        await execute_publish_work(
            cast(Application, app),
            _request(
                request_tail=211,
                event_tail=212,
                action_tail=213,
                expected_frontier={"sequence": "0", "head_digest": "genesis"},
            ),
        )

    assert caught.value.code is PublicErrorCode.FRONTIER_CONFLICT
    assert caught.value.retryable is True
    assert caught.value.safe_details.get("reason_code") == "frontier_changed"
    # The conflict must carry the *current* head so a caller can retry without a status round-trip.
    assert caught.value.safe_details.get("sequence") == first.result_frontier.sequence
    assert caught.value.safe_details.get("head_digest") == first.result_frontier.head_digest


async def test_mutated_event_drafts_same_request_id_is_request_identity_conflict() -> None:
    """Run-3 sequence: same request_id with a different body must not re-append or invalidate."""

    app, objects = _composition()
    original = _request(
        expected_frontier={"sequence": "0", "head_digest": "genesis"},
        event_drafts=(
            _draft(event_tail=401, action_tail=402),
            _draft(event_tail=403, action_tail=404),
        ),
    )
    first = await execute_publish_work(cast(Application, app), original)
    durable_after_first = len(objects._data)  # pyright: ignore[reportPrivateUsage]

    mutated = _request(
        expected_frontier={"sequence": "0", "head_digest": "genesis"},
        event_drafts=(
            _draft(event_tail=401, action_tail=402),
            {
                **_draft(event_tail=403, action_tail=404),
                "payload": {
                    "action_id": "act_00000000-0000-4000-8000-000000000404",
                    "action_kind": "other",
                    "description": "Mutated draft body that is no longer the original",
                },
            },
        ),
    )
    with pytest.raises(PublicOperationError) as caught:
        await execute_publish_work(cast(Application, app), mutated)

    assert caught.value.code is PublicErrorCode.REQUEST_IDENTITY_CONFLICT
    assert caught.value.safe_details.get("reason_code") == "request_identity_conflict"
    assert caught.value.safe_details.get("sequence") == first.result_frontier.sequence
    assert caught.value.safe_details.get("head_digest") == first.result_frontier.head_digest
    assert caught.value.safe_details.get("count") == len(first.accepted_events)
    assert len(objects._data) == durable_after_first  # pyright: ignore[reportPrivateUsage]


async def test_status_view_operation_returns_stored_publish_result() -> None:
    app, _ = _composition()
    request = _request(request_tail=501, event_tail=502, action_tail=503)
    first = await execute_publish_work(cast(Application, app), request)
    seed = append_command()

    status = await execute_status(
        cast(StatusApplication, app),
        StatusRequest.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": "req_00000000-0000-4000-8000-000000000510",
                "session_id": seed.session_id,
                "writer_id": seed.writer_id,
                "view": "operation",
                "limit": "1",
                "filter": {"operation_request_id": request.request_id},
                "actor": {"actor_id": "harness:test", "actor_type": "harness"},
                "client": {
                    "kind": "test_client",
                    "version": "0.1.0",
                    "integration": "local_cli",
                },
            }
        ),
    )

    assert status.view == "operation"
    page = status.page
    assert type(page) is StatusOperationPageModel
    assert page.found is True
    assert page.state == "complete"
    assert page.operation_kind == "publish_work"
    assert page.outcome == "accepted"
    assert page.result_frontier is not None
    assert page.result_frontier.sequence == str(first.result_frontier.sequence)
    assert tuple(item.event_id for item in page.accepted_events) == tuple(
        item.event_id for item in first.accepted_events
    )
    assert tuple(item.entry_digest for item in page.accepted_events) == tuple(
        item.entry_digest for item in first.accepted_events
    )


async def test_status_view_operation_absent_for_unknown_request_id() -> None:
    app, _ = _composition()
    request = _request(request_tail=521)
    await execute_publish_work(cast(Application, app), request)
    seed = append_command()

    unknown = await execute_status(
        cast(StatusApplication, app),
        StatusRequest.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": "req_00000000-0000-4000-8000-000000000522",
                "session_id": seed.session_id,
                "writer_id": seed.writer_id,
                "view": "operation",
                "limit": "1",
                "filter": {"operation_request_id": "req_00000000-0000-4000-8000-000000000599"},
                "actor": {"actor_id": "harness:test", "actor_type": "harness"},
                "client": {
                    "kind": "test_client",
                    "version": "0.1.0",
                    "integration": "local_cli",
                },
            }
        ),
    )
    page = cast(StatusOperationPageModel, unknown.page)
    assert page.found is False
    assert page.state == "absent"
    assert page.accepted_events == ()

    # Cross-writer lookup: operations are keyed by (writer_id, request_id). A different writer
    # identity for the same session reports absent rather than leaking another writer's result.
    foreign_writer = "wri_00000000-0000-4000-8000-000000000599"
    assert foreign_writer != seed.writer_id
    # The memory harness routes a single TaskRuntime writer; foreign writer_id cannot be routed
    # without SESSION_CONFLICT. Prove the ledger lookup keying instead (same contract the status
    # application uses before projecting the recovery page).
    assert (
        await app.runtime.task.ledger.lookup_operation(foreign_writer, request.request_id)
    ) is None
    assert (
        await app.runtime.task.ledger.lookup_operation(seed.writer_id, request.request_id)
    ) is not None


async def test_dry_run_appends_nothing_and_leaves_request_id_reusable() -> None:
    """dry_run validates without durable effects; the same request_id still publishes later."""

    app, objects = _composition()
    frontier_before = await app.runtime.task.ledger.load_frontier()
    durable_before = len(objects._data)  # pyright: ignore[reportPrivateUsage]
    request = _request(
        request_tail=601,
        event_tail=602,
        action_tail=603,
        expected_frontier={"sequence": "0", "head_digest": "genesis"},
    )
    preview_payload = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    preview_payload["dry_run"] = True
    preview_request = PublishWorkRequestModel.model_validate(preview_payload)

    preview = await execute_publish_work(cast(Application, app), preview_request)

    assert preview.ok is True
    assert preview.outcome == "dry_run"
    assert preview.subject_frontier.sequence == str(frontier_before.sequence)
    assert preview.result_frontier == preview.subject_frontier
    assert type(preview.root) is PublishWorkDryRunModel
    assert preview.root.evidential is False
    assert len(preview.root.would_accept) == 1
    assert preview.root.would_accept[0].event_id == request.event_drafts[0]["event_id"]

    frontier_after_preview = await app.runtime.task.ledger.load_frontier()
    assert frontier_after_preview.sequence == frontier_before.sequence
    assert frontier_after_preview.head_digest == frontier_before.head_digest
    assert len(objects._data) == durable_before  # pyright: ignore[reportPrivateUsage]
    assert (
        await app.runtime.task.ledger.lookup_operation(request.writer_id, request.request_id)
    ) is None

    accepted = await execute_publish_work(cast(Application, app), request)
    assert accepted.ok is True
    assert accepted.outcome == "accepted"
    assert accepted.result_frontier.sequence != str(frontier_before.sequence)
    assert (
        await app.runtime.task.ledger.lookup_operation(request.writer_id, request.request_id)
    ) is not None
