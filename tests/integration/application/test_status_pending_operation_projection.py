"""Projecting the recovery view for a check stranded mid-semantic-dispatch.

The 2026-07-30 dogfood run left a check pending in ``SEMANTIC_WAIT`` and every subsequent
``status(view=operation)`` failed with an ``AttributeError``, recorded by the daemon as
``read_projection_failed``. Nothing reproduced it because the projection sweep points
``view=operation`` at a *completed publish* — the one operation kind whose recovery page carries
nested accepted events — and the application-level status tests seed ``CheckPhase.RESERVED`` with
no semantic job. Neither is the shape that broke: a *pending* operation projects a page where
every optional field is absent.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from builders.projection_workflow import (
    ProjectionCase,
    project_case,
    request_base,
    run_projection_workflow,
)
from builders.start_application import protocol_id
from yoetz.application.status import StatusInternalResult
from yoetz.ports.control import ControlMethod
from yoetz.ports.ledger import CheckPhase, OperationKind, OperationRecord, OperationState
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.models import StatusRequest

pytestmark = pytest.mark.anyio


def _stranded_check(writer_id: str, operation_id: str, task_id: str) -> OperationRecord:
    """A check parked mid-dispatch: pending, SEMANTIC_WAIT, deterministic result already durable."""

    resume = ObjectRef(
        "obj_00000000-0000-4000-8000-0000000000cc",
        1,
        "hmac-sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
        "yoetz-object/1",
        "bmk-1",
        ObjectMetadata(
            ObjectKind.DETERMINISTIC_RESULT,
            "application/vnd.yoetz.deterministic-result+json",
            task_id,
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    return OperationRecord(
        writer_id,
        operation_id,
        OperationKind.CHECK,
        "sha256:" + "c" * 64,
        OperationState.PENDING,
        CheckPhase.SEMANTIC_WAIT,
        "owner-generation-1",
        "lease-owner-1",
        1,
        datetime(2030, 1, 1, tzinfo=UTC),
        resume,
        None,
        None,
        None,
        None,
        None,
    )


async def test_pending_check_operation_page_projects_to_the_client() -> None:
    """A caller who got OPERATION_PENDING must be able to read back what is pending.

    This drives the daemon's exact post-commit projection, not just ``execute_status``: the
    production failure was recorded against ``component=service.daemon``, so the application layer
    alone would have reported success while the caller still received an error.
    """

    workflow = await run_projection_workflow()
    app = workflow.app
    started = workflow.case("start").internal
    session_id = cast(str, getattr(started, "session_id"))
    writer_id = cast(str, getattr(started, "writer_id"))
    task_id = cast(str, getattr(started, "task_id"))

    operation_id = "req_00000000-0000-4000-8000-0000000009a1"
    status_body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", 9102)),
        "session_id": session_id,
        "writer_id": writer_id,
        "view": "operation",
        "limit": "10",
        "filter": {"operation_request_id": operation_id},
    }
    _seed_operation(app, session_id, writer_id, operation_id, task_id)

    internal = await app.status(StatusRequest.model_validate(status_body))
    assert type(internal) is StatusInternalResult

    projected = await project_case(
        app,
        ProjectionCase("status/operation-pending", ControlMethod.STATUS, status_body, internal),
        9100,
    )
    page = cast(dict[str, JsonValue], projected["page"])
    assert page["found"] is True
    assert page["state"] == "pending"
    assert page["operation_kind"] == "check"


def _seed_operation(
    app: object, session_id: str, writer_id: str, operation_id: str, task_id: str
) -> None:
    """Inject the stranded record directly; paying for a real stranded check is not the point."""

    runtime = getattr(app, "runtime")
    resources = getattr(runtime, "resources")
    ledger, _objects = next(iter(resources.values()))
    ledger._state.operations[(writer_id, operation_id)] = (  # pyright: ignore[reportPrivateUsage]
        _stranded_check(writer_id, operation_id, task_id),
        None,
    )
