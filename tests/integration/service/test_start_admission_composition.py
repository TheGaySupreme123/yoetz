"""Real service/socket start-admission composition for same-workspace reattachment.

The isolated service fixture keeps this regression at the public MCP boundary: the test exercises
the daemon, Unix socket, SQLite start catalog, bundle runtime, lifecycle ledger, and response
replay together.  It does not provide native host acceptance evidence.
"""

from __future__ import annotations

from typing import cast

import pytest

import yoetz.mcp.server as bridge
from integration.service.test_start_contention_composition import (
    _body,  # pyright: ignore[reportPrivateUsage]
    _logical_result,  # pyright: ignore[reportPrivateUsage]
    _structured,  # pyright: ignore[reportPrivateUsage]
    ready,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    runtime_directory,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from yoetz.application.service import Application
from yoetz.ports.start_catalog import StartIdentityInput
from yoetz.protocol.canonical import JsonValue

pytestmark = pytest.mark.anyio


def _admission_body(
    seed: int,
    *,
    workspace_ref: str,
    external_ref: str,
    mode: str = "create_or_attach",
    session_id: str | None = None,
) -> dict[str, JsonValue]:
    body = dict(_body(seed, session_id))
    body["mode"] = mode
    body["workspace_ref"] = workspace_ref
    body["external_ref"] = external_ref
    return body


async def test_same_workspace_admission_reattaches_exact_pair_and_replays(
    ready: tuple[Application, bridge.BridgeRuntime],  # noqa: F811
) -> None:
    """A new pair creates a sibling while its exact pair remains an attach selector.

    This is the smallest service-level reproduction of the 0.3 admission change.  A workspace
    may have several independently named tasks, but a repeated exact pair must never create a
    second task and replaying its request id must return the committed attachment unchanged.
    """

    _application, transport = ready
    workspace = "workspace-810"
    first = _structured(
        await bridge.dispatch_start(
            _admission_body(
                8100,
                workspace_ref=workspace,
                external_ref="external-A",
                mode="create",
            ),
            transport,
        )
    )
    assert first["ok"] is True
    assert first["outcome"] == "created"

    drifted_request = _admission_body(
        8101,
        workspace_ref=workspace,
        external_ref="external-B",
    )
    sibling = _structured(await bridge.dispatch_start(drifted_request, transport))
    assert sibling["ok"] is True
    assert sibling["outcome"] == "created"
    assert sibling["task_id"] != first["task_id"]

    exact_request = _admission_body(
        8102,
        workspace_ref=workspace,
        external_ref="external-A",
    )
    attached = _structured(await bridge.dispatch_start(exact_request, transport))
    assert attached["ok"] is True
    assert attached["outcome"] == "attached"
    assert attached["task_id"] == first["task_id"]
    assert attached["session_id"] != first["session_id"]

    replayed = _structured(await bridge.dispatch_start(exact_request, transport))
    assert _logical_result(replayed) == _logical_result(attached)


async def test_start_admission_rejects_cross_workspace_selector(
    ready: tuple[Application, bridge.BridgeRuntime],  # noqa: F811
) -> None:
    """A selector never crosses its workspace boundary or creates a hidden third task."""

    application, transport = ready
    first = _structured(
        await bridge.dispatch_start(
            _admission_body(
                8110,
                workspace_ref="workspace-811-A",
                external_ref="external-A",
                mode="create",
            ),
            transport,
        )
    )
    unrelated = _structured(
        await bridge.dispatch_start(
            _admission_body(
                8111,
                workspace_ref="workspace-811-B",
                external_ref="external-B",
            ),
            transport,
        )
    )
    assert unrelated["ok"] is True
    assert unrelated["outcome"] == "created"
    identity = await application.start_catalog.commit_identity(
        StartIdentityInput(
            "Synthetic start contention",
            "workspace-811-B",
            "external-B",
        )
    )
    workspace_tasks_before = await application.start_catalog.list_workspace_task_ids(
        cast(str, identity.workspace_ref_commitment)
    )
    assert workspace_tasks_before == (cast(str, unrelated["task_id"]),)
    request = _admission_body(
        8112,
        workspace_ref="workspace-811-B",
        external_ref="external-C",
        mode="attach",
        session_id=cast(str, first["session_id"]),
    )
    result = _structured(await bridge.dispatch_start(request, transport))
    error = cast(dict[str, object], result["error"])
    assert error["code"] == "SESSION_CONFLICT"
    assert (
        await application.start_catalog.list_workspace_task_ids(
            cast(str, identity.workspace_ref_commitment)
        )
        == workspace_tasks_before
    )


async def test_ambiguous_same_workspace_selector_stays_conflict_but_session_attach_recovers(
    ready: tuple[Application, bridge.BridgeRuntime],  # noqa: F811
) -> None:
    """A second root keeps a new-pair selector ambiguous; the held session remains usable."""

    application, transport = ready
    workspace = "workspace-812"
    first = _structured(
        await bridge.dispatch_start(
            _admission_body(
                8120,
                workspace_ref=workspace,
                external_ref="external-A",
                mode="create",
            ),
            transport,
        )
    )
    sibling = _structured(
        await bridge.dispatch_start(
            _admission_body(
                8121,
                workspace_ref=workspace,
                external_ref="external-B",
            ),
            transport,
        )
    )
    assert first["ok"] is True
    assert sibling["ok"] is True
    assert first["task_id"] != sibling["task_id"]

    identity = await application.start_catalog.commit_identity(
        StartIdentityInput("Synthetic start contention", workspace, "external-A")
    )
    workspace_tasks_before = await application.start_catalog.list_workspace_task_ids(
        cast(str, identity.workspace_ref_commitment)
    )
    assert len(workspace_tasks_before) == 2
    assert set(workspace_tasks_before) == {
        cast(str, first["task_id"]),
        cast(str, sibling["task_id"]),
    }

    third_pair = _admission_body(
        8122,
        workspace_ref=workspace,
        external_ref="external-C",
        mode="attach",
        session_id=cast(str, first["session_id"]),
    )
    refused = _structured(await bridge.dispatch_start(third_pair, transport))
    error = cast(dict[str, object], refused["error"])
    assert error["code"] == "SESSION_CONFLICT"
    assert (
        await application.start_catalog.list_workspace_task_ids(
            cast(str, identity.workspace_ref_commitment)
        )
        == workspace_tasks_before
    )

    session_only = _structured(
        await bridge.dispatch_start(_body(8123, cast(str, first["session_id"])), transport)
    )
    assert session_only["ok"] is True
    assert session_only["outcome"] == "attached"
    assert session_only["task_id"] == first["task_id"]
    assert session_only["session_id"] != first["session_id"]
