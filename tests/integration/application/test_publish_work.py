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
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort
from yoetz.ports.objects import ObjectStorePort
from yoetz.ports.runtime import RouteCommand, TaskRuntime
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import PublishWorkRequestModel

pytestmark = pytest.mark.anyio


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
        frozenset({RuntimeCapability.WRITE}),
        ledger,
        cast(ObjectStorePort, objects),
        cast(ImporterPort, object()),
        "0.1.0",
        "0.1.0",
        "0.1",
        "1.0.0",
        ownership_fence(),
    )
    runtime = _Runtime(task)
    return _Application(runtime), objects


def _request(
    *,
    family: str = "action_recorded",
    description: str = "Materialized one coherent slice",
    request_tail: int = 201,
    event_tail: int = 202,
    action_tail: int = 203,
    expected_frontier: object = None,
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
            "event_drafts": (
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
    assert caught.value.safe_details == {"reason_code": "event_family_not_admitted"}
    assert len(objects._data) == before  # pyright: ignore[reportPrivateUsage]
    assert app.runtime.release_count == 1


async def test_same_request_id_replay_returns_the_stored_result_without_rewriting() -> None:
    """The recovery the MCP post-commit error advertises must actually work.

    When response shaping fails after a durable commit, the bridge tells the caller to retry with
    the same request_id. That guidance is only honest if an identical replay returns the committed
    result instead of appending a second event.
    """

    app, objects = _composition()
    first = await execute_publish_work(cast(Application, app), _request())
    durable_after_first = len(objects._data)  # pyright: ignore[reportPrivateUsage]

    second = await execute_publish_work(cast(Application, app), _request())

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
    await execute_publish_work(cast(Application, app), _request())
    durable_after_first = len(objects._data)  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(PublicOperationError) as caught:
        await execute_publish_work(
            cast(Application, app), _request(description="Changed logical work")
        )

    assert caught.value.code is PublicErrorCode.IDEMPOTENCY_CONFLICT
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
