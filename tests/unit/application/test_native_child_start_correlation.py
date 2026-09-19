"""Native start correlation never lets response IDs discover or select a task."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from yoetz.application.observation_coordinator import ObservationCoordinator
from yoetz.cli.observe_hooks import map_hook_payload_to_envelope
from yoetz.domain.observation import ObservationEnvelope
from yoetz.ports.runtime import TaskRuntime
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.ids import IdKind, new_id

pytestmark = pytest.mark.anyio


def _callback() -> tuple[dict[str, JsonValue], dict[str, str]]:
    ids = {
        name: new_id(kind)
        for name, kind in (
            ("task_id", IdKind.TASK),
            ("session_id", IdKind.SESSION),
            ("writer_id", IdKind.WRITER),
            ("parent_task_id", IdKind.TASK),
        )
    }
    return {
        "session_id": "native-session",
        "subagent_id": "worker",
        "tool_use_id": "attach-call",
        "parent_tool_call_id": "spawn-call",
        "tool_name": "mcp__yoetz__start",
        "tool_response": {"ok": True, "outcome": "attached", **ids},
    }, ids


def _envelope(payload: dict[str, JsonValue]) -> ObservationEnvelope:
    return map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment="hmac-sha256:" + "a" * 64,
        event_ordinal=1,
        key_material=b"k" * 32,
        source_generation=1,
    )


@pytest.mark.parametrize("wrong_field", ["task_id", "session_id", "writer_id", "parent_task_id"])
async def test_bridge_rejects_response_identity_before_recording(wrong_field: str) -> None:
    payload, ids = _callback()
    registry = SimpleNamespace(record_host_lineage_observation=AsyncMock())
    catalog = SimpleNamespace(
        task_lineage=AsyncMock(return_value=SimpleNamespace(parent_task_id=ids["parent_task_id"]))
    )
    coordinator = cast(
        ObservationCoordinator,
        SimpleNamespace(
            host_lineage_registry=registry, lineage_coordinator=SimpleNamespace(catalog=catalog)
        ),
    )
    runtime = cast(
        TaskRuntime,
        SimpleNamespace(
            task_id=ids["task_id"], session_id=ids["session_id"], writer_id=ids["writer_id"]
        ),
    )
    result = cast(dict[str, JsonValue], payload["tool_response"])
    result[wrong_field] = new_id(
        {
            "task_id": IdKind.TASK,
            "session_id": IdKind.SESSION,
            "writer_id": IdKind.WRITER,
            "parent_task_id": IdKind.TASK,
        }[wrong_field]
    )
    gap = await ObservationCoordinator._bind_native_child_start(  # pyright: ignore[reportPrivateUsage]
        coordinator,
        runtime,
        _envelope(payload),
        writer_routes=(),
    )
    assert gap == "host_lineage_identity_conflict"
    registry.record_host_lineage_observation.assert_not_awaited()
    if wrong_field != "parent_task_id":
        catalog.task_lineage.assert_not_awaited()


@pytest.mark.parametrize(
    "identity",
    [
        "missing",
        "conflicting",
        "invalid_parent",
        "invalid_tool_use",
        "invalid_tool_call",
        "conflicting_calls",
        "null_call",
    ],
)
async def test_bridge_missing_native_identity_cannot_make_annotation(identity: str) -> None:
    payload, ids = _callback()
    if identity == "missing":
        payload.pop("subagent_id")
    elif identity == "conflicting":
        payload["agent_id"] = "another-worker"
    elif identity == "invalid_parent":
        payload["parent_tool_call_id"] = {"not": "a-token"}
    elif identity == "conflicting_calls":
        payload["tool_call_id"] = "another-attach-call"
    elif identity == "null_call":
        payload["tool_call_id"] = None
    else:
        payload.pop("parent_tool_call_id")
        payload["tool_use_id" if identity == "invalid_tool_use" else "tool_call_id"] = {
            "not": "a-token"
        }
    registry = SimpleNamespace(record_host_lineage_observation=AsyncMock())
    catalog = SimpleNamespace(
        task_lineage=AsyncMock(return_value=SimpleNamespace(parent_task_id=ids["parent_task_id"]))
    )
    coordinator = cast(
        ObservationCoordinator,
        SimpleNamespace(
            host_lineage_registry=registry, lineage_coordinator=SimpleNamespace(catalog=catalog)
        ),
    )
    runtime = cast(
        TaskRuntime,
        SimpleNamespace(
            task_id=ids["task_id"], session_id=ids["session_id"], writer_id=ids["writer_id"]
        ),
    )
    gap = await ObservationCoordinator._bind_native_child_start(  # pyright: ignore[reportPrivateUsage]
        coordinator,
        runtime,
        _envelope(payload),
        writer_routes=(),
    )
    assert gap == "missing_subagent_identity"
    registry.record_host_lineage_observation.assert_not_awaited()


@pytest.mark.parametrize("parent_call", ["spawn-call", None])
async def test_bridge_uses_admitted_history_and_spawn_call_not_attach_call(
    parent_call: str | None,
) -> None:
    payload, ids = _callback()
    payload["tool_call_id"] = "attach-call"
    if parent_call is None:
        payload.pop("parent_tool_call_id")
    registry = SimpleNamespace(
        record_host_lineage_observation=AsyncMock(
            return_value=SimpleNamespace(correlation_id="corr")
        ),
        bind_provisional_annotation=AsyncMock(),
    )
    catalog = SimpleNamespace(
        task_lineage=AsyncMock(return_value=SimpleNamespace(parent_task_id=ids["parent_task_id"]))
    )
    coordinator = cast(
        ObservationCoordinator,
        SimpleNamespace(
            host_lineage_registry=registry, lineage_coordinator=SimpleNamespace(catalog=catalog)
        ),
    )
    runtime = cast(
        TaskRuntime,
        SimpleNamespace(
            task_id=ids["task_id"],
            session_id=new_id(IdKind.SESSION),
            writer_id=new_id(IdKind.WRITER),
        ),
    )
    gap = await ObservationCoordinator._bind_native_child_start(  # pyright: ignore[reportPrivateUsage]
        coordinator,
        runtime,
        _envelope(payload),
        writer_routes=((ids["session_id"], ids["writer_id"]),),
    )
    assert gap is None
    call = registry.record_host_lineage_observation.await_args
    assert call is not None
    assert call.args[0] == ids["parent_task_id"]
    assert call.args[1].correlation.parent_tool_call_id == parent_call
    registry.bind_provisional_annotation.assert_awaited_once_with(
        ids["parent_task_id"], "corr", ids["task_id"]
    )


def test_only_successful_owned_start_response_supplies_bridge_facts() -> None:
    payload, _ids = _callback()
    envelope = _envelope(payload)
    assert "lineage_child_task_id" in envelope.structural_payload
    variants: tuple[dict[str, JsonValue], ...] = (
        {**payload, "tool_name": "foreign_start"},
        {**payload, "tool_response": {"ok": False}},
        {**payload, "tool_response": {"ok": True, "outcome": "delegated"}},
        {**payload, "tool_response": {}, "lineage_child_task_id": new_id(IdKind.TASK)},
    )
    for changed in variants:
        assert "lineage_child_task_id" not in _envelope(changed).structural_payload


@pytest.mark.parametrize(
    ("disposition", "age", "rotated", "event_kind", "expected"),
    [
        ("accepted", 0, False, "PostToolUse", True),
        ("accepted", 60, False, "PostToolUse", True),
        ("duplicate", 0, False, "PostToolUse", False),
        ("accepted", 61, False, "PostToolUse", False),
        ("accepted", -1, False, "PostToolUse", False),
        ("accepted", 0, True, "PostToolUse", False),
        ("accepted", 0, False, "SessionEnd", False),
        ("accepted", 0, False, "SubagentStop", False),
    ],
)
async def test_only_fresh_current_native_activity_renews_lease(
    disposition: str, age: int, rotated: bool, event_kind: str, expected: bool
) -> None:
    from dataclasses import replace
    from datetime import timedelta

    from yoetz.application.observation_materialize import observation_writer_id
    from yoetz.domain.observation import ObservationIngestDisposition

    payload, ids = _callback()
    envelope = replace(_envelope(payload), event_kind=event_kind)
    hook = AsyncMock()
    coordinator = cast(
        ObservationCoordinator,
        SimpleNamespace(
            observed_activity_hook=hook,
            observed_activity_max_age_seconds=60,
            clock=SimpleNamespace(
                now_utc=lambda: envelope.receipt_time.as_datetime() + timedelta(seconds=age)
            ),
        ),
    )
    runtime = cast(
        TaskRuntime,
        SimpleNamespace(
            task_id=ids["task_id"],
            writer_id=observation_writer_id(ids["task_id"], ids["session_id"]),
            session_id=new_id(IdKind.SESSION) if rotated else ids["session_id"],
        ),
    )
    await ObservationCoordinator._renew_observed_activity(  # pyright: ignore[reportPrivateUsage]
        coordinator,
        runtime,
        envelope,
        disposition=ObservationIngestDisposition(disposition),
        predecessor_session_id=ids["session_id"],
        predecessor_writer_id=ids["writer_id"],
    )
    assert hook.await_count == int(expected)


@pytest.mark.parametrize("parent_alias", ["call-b", {"not": "a-token"}])
def test_subagent_hook_conflicting_parent_alias_cannot_weaken_identity(
    parent_alias: JsonValue,
) -> None:
    from yoetz.application.observation_materialize import materialize_observation_envelope
    from yoetz.domain.host_lineage import host_lineage_from_envelope

    payload, ids = _callback()
    payload["tool_use_id"] = parent_alias
    envelope = map_hook_payload_to_envelope(
        "SubagentStart",
        payload,
        session_commitment="hmac-sha256:" + "a" * 64,
        event_ordinal=1,
        key_material=b"k" * 32,
        source_generation=1,
    )
    assert host_lineage_from_envelope(envelope) is None
    batch = materialize_observation_envelope(envelope, task_id=ids["task_id"])
    assert batch.skip_reason == "missing_subagent_identity"
    assert batch.drafts == ()
