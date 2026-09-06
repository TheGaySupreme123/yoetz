"""Named #509 scenarios through the production service and encrypted task bundles.

These rows supplement the observation worker concurrency scenario: a worker's check result is
not a public task check or a task receipt. Each row below invokes the actual public operations.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from builders.multi_agent import multi_agent_service
from yoetz.application.publish_work import PublishWorkInternalResult
from yoetz.application.start import StartInternalResult
from yoetz.config.models import LineageSettings, YoetzConfig
from yoetz.domain.coordination import WorkState
from yoetz.ports.control import RepositoryPrivacyContext
from yoetz.ports.ledger import CheckCommitResult
from yoetz.ports.start_catalog import TaskRouteState
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import (
    CheckRequest,
    PublishWorkRequest,
    ReceiptRequest,
    StartRequest,
    StatusLineagePageModel,
    StatusRequest,
)

pytestmark = pytest.mark.anyio

# This is the trusted control boundary's resolved Git-common-root identity. The public
# workspace_ref remains only a task selector and is never used as disclosure authority.
_REPOSITORY = RepositoryPrivacyContext("hmac-sha256:" + "d" * 64, "git_common_root")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _identity() -> dict[str, object]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": new_id(IdKind.REQUEST),
        "actor": {"actor_id": "harness:conformance", "actor_type": "harness"},
        "client": {
            "kind": "cooperative_agent",
            "version": "0.1.0",
            "integration": "cooperative_mcp",
        },
    }


def _workspace(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    return root.resolve()


async def test_explicit_siblings_have_independent_public_checks_receipts_and_work_state(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        tasks: list[StartInternalResult] = []
        for index in range(2):
            tasks.append(
                await app.start(
                    StartRequest.model_validate(
                        {
                            **_identity(),
                            "mode": "create",
                            "task_title": f"Sibling {index}",
                            "workspace_ref": str(workspace),
                            "external_ref": f"sibling-{index}",
                            "requested_view": "compact",
                        }
                    ),
                    repository_privacy_context=_REPOSITORY,
                )
            )
        first, second = tasks
        assert first.task_id != second.task_id
        assert first.session_id != second.session_id
        assert first.writer_id != second.writer_id

        receipt_ids: set[str] = set()
        for task in tasks:
            checked = await app.check(
                CheckRequest.model_validate(
                    {
                        **_identity(),
                        "session_id": task.session_id,
                        "writer_id": task.writer_id,
                        "expected_frontier": task.frontier.model_dump(mode="json"),
                        "mode": "deterministic_only",
                        "max_findings": "10",
                        "policy_packs": ["work-integrity/0.1.0"],
                    }
                ),
                repository_privacy_context=_REPOSITORY,
            )
            assert isinstance(checked, CheckCommitResult)
            assert checked.task_id == task.task_id
            assert checked.outcome == "committed"
            receipt_request = ReceiptRequest.model_validate(
                {
                    **_identity(),
                    "task_id": task.task_id,
                    "session_id": task.session_id,
                    "writer_id": task.writer_id,
                    "expected_frontier": dict(checked.result_frontier.as_wire().items()),
                    "format": "json",
                    "include": "standard",
                    "redaction_profile": "default_local_export",
                }
            )
            receipt = await app.receipt(receipt_request, repository_privacy_context=_REPOSITORY)
            receipt_ids.add(receipt.receipt_id)
            assert receipt.task_id == task.task_id
            assert receipt.document is not None
            assert (
                await app.receipt(receipt_request, repository_privacy_context=_REPOSITORY)
            ).receipt_digest == receipt.receipt_digest
            lineage = await app.start_catalog.task_lineage(task.task_id)
            assert lineage is not None and lineage.work_state is WorkState.OPEN
            status = await app.status(
                StatusRequest.model_validate(
                    {
                        **_identity(),
                        "session_id": task.session_id,
                        "writer_id": task.writer_id,
                        "view": "lineage",
                        "limit": "10",
                    }
                ),
                repository_privacy_context=_REPOSITORY,
            )
            assert status.task_id == task.task_id
            assert isinstance(status.page, StatusLineagePageModel)
            assert status.page.children == ()
        assert len(receipt_ids) == 2


@pytest.mark.parametrize("same_pair", [True, False])
async def test_concurrent_create_or_attach_pairs_are_resolved_atomically(
    tmp_path: Path,
    same_pair: bool,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        requests = tuple(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create_or_attach",
                    "task_title": f"Concurrent {index}",
                    "workspace_ref": str(workspace),
                    "external_ref": "shared-pair" if same_pair else f"independent-{index}",
                    "requested_view": "compact",
                }
            )
            for index in range(2)
        )
        results = await asyncio.gather(
            *(
                service.app.start(request, repository_privacy_context=_REPOSITORY)
                for request in requests
            )
        )
        assert len({result.task_id for result in results}) == (1 if same_pair else 2)
        projects = tuple(
            [
                await service.app.start_catalog.list_task_project_ids(result.task_id)
                for result in results
            ]
        )
        if same_pair:
            assert projects == ((), ()), "two sessions on one task cannot create a project"
        else:
            assert len(projects[0]) == 1
            assert projects[0] == projects[1]
            assert set(await service.app.start_catalog.list_project_task_ids(projects[0][0])) == {
                result.task_id for result in results
            }
        for request, original in zip(requests, results, strict=True):
            replay = await service.app.start(request, repository_privacy_context=_REPOSITORY)
            assert (replay.task_id, replay.session_id, replay.writer_id) == (
                original.task_id,
                original.session_id,
                original.writer_id,
            )


async def test_real_delegation_attach_publication_and_child_receipt_preserve_parent_lane(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        parent = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        parent_binding = await app.start_catalog.session_binding(parent.session_id)
        delegate_request = StartRequest.model_validate(
            {
                **_identity(),
                "mode": "delegate",
                "task_title": "Child",
                "session_id": parent.session_id,
                "requested_view": "compact",
            }
        )
        delegated = await app.start(delegate_request, repository_privacy_context=_REPOSITORY)
        assert delegated.attach_handle is not None
        child_route = await app.start_catalog.task_route(delegated.task_id)
        assert child_route is not None
        assert child_route.state is TaskRouteState.INITIALIZING
        replayed = await app.start(delegate_request, repository_privacy_context=_REPOSITORY)
        assert replayed.task_id == delegated.task_id
        assert replayed.attach_handle == delegated.attach_handle
        after_delegation = await app.start_catalog.session_binding(parent.session_id)
        assert parent_binding is not None and after_delegation is not None
        assert (
            after_delegation.task_id,
            after_delegation.session_id,
            after_delegation.writer_id,
        ) == (
            parent_binding.task_id,
            parent_binding.session_id,
            parent_binding.writer_id,
        )
        attached = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "attach",
                    "task_title": "Child",
                    "attach_handle": delegated.as_wire()["attach_handle"],
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert attached.task_id == delegated.task_id
        assert attached.task_id != parent.task_id
        publication = await app.publish_work(
            PublishWorkRequest.model_validate(
                {
                    **_identity(),
                    "session_id": attached.session_id,
                    "writer_id": attached.writer_id,
                    "expected_frontier": attached.frontier.model_dump(mode="json"),
                    "event_drafts": [
                        {
                            "event_id": new_id(IdKind.EVENT),
                            "schema": {"name": "action_recorded", "version": "1.0.0"},
                            "occurred_at": "2026-09-05T12:00:00.000Z",
                            "causal_parents": [],
                            "payload": {
                                "action_id": new_id(IdKind.ACTION),
                                "action_kind": "review",
                                "description": "Child-only marker review",
                            },
                            "artifact_refs": [],
                            "evidence_refs": [],
                        }
                    ],
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert isinstance(publication, PublishWorkInternalResult)
        assert publication.task_id == attached.task_id
        checked = await app.check(
            CheckRequest.model_validate(
                {
                    **_identity(),
                    "session_id": attached.session_id,
                    "writer_id": attached.writer_id,
                    "expected_frontier": dict(publication.result_frontier.as_wire().items()),
                    "mode": "deterministic_only",
                    "max_findings": "10",
                    "policy_packs": ["work-integrity/0.1.0"],
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert isinstance(checked, CheckCommitResult)
        receipt = await app.receipt(
            ReceiptRequest.model_validate(
                {
                    **_identity(),
                    "task_id": attached.task_id,
                    "session_id": attached.session_id,
                    "writer_id": attached.writer_id,
                    "expected_frontier": dict(checked.result_frontier.as_wire().items()),
                    "format": "json",
                    "include": "standard",
                    "redaction_profile": "default_local_export",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert receipt.task_id == attached.task_id
        assert isinstance(receipt.document, Mapping)
        assert receipt.document["task_id"] == attached.task_id
        lineage = await app.status(
            StatusRequest.model_validate(
                {
                    **_identity(),
                    "session_id": parent.session_id,
                    "writer_id": parent.writer_id,
                    "view": "lineage",
                    "limit": "10",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert isinstance(lineage.page, StatusLineagePageModel)
        (child,) = lineage.page.children
        assert child.task_id == attached.task_id
        assert child.origin == "parent_minted"
        assert child.acceptance == "accepted"
        assert child.work_state == "open"
        assert child.rollup_state != "clean"


async def test_delegated_child_does_not_consume_parent_selector(
    tmp_path: Path,
) -> None:
    """Child source commitments stay visible to lineage without becoming start selectors."""

    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        parent = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Selector parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "selector-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        delegated = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "delegate",
                    "task_title": "Selector child",
                    "session_id": parent.session_id,
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        resumed = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create_or_attach",
                    "task_title": "Selector parent resumed",
                    "workspace_ref": str(workspace),
                    "external_ref": "selector-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert resumed.task_id == parent.task_id
        assert delegated.task_id != resumed.task_id


async def test_ready_config_enforces_depth_and_fanout_bounds(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    depth_config = YoetzConfig(lineage=LineageSettings(max_depth=0))
    async with multi_agent_service(tmp_path / "depth-state", config=depth_config) as service:
        parent = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Depth parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "depth-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        with pytest.raises(PublicOperationError) as depth_error:
            await service.app.start(
                StartRequest.model_validate(
                    {
                        **_identity(),
                        "mode": "delegate",
                        "task_title": "Depth child",
                        "session_id": parent.session_id,
                        "requested_view": "compact",
                    }
                ),
                repository_privacy_context=_REPOSITORY,
            )
        assert depth_error.value.code is PublicErrorCode.LIMIT_EXCEEDED

    fanout_config = YoetzConfig(lineage=LineageSettings(max_fanout=1))
    async with multi_agent_service(tmp_path / "fanout-state", config=fanout_config) as service:
        parent = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Fanout parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "fanout-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        for title in ("Fanout child one", "Fanout child two"):
            request = StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "delegate",
                    "task_title": title,
                    "session_id": parent.session_id,
                    "requested_view": "compact",
                }
            )
            if title.endswith("one"):
                await service.app.start(request, repository_privacy_context=_REPOSITORY)
                continue
            with pytest.raises(PublicOperationError) as fanout_error:
                await service.app.start(request, repository_privacy_context=_REPOSITORY)
            assert fanout_error.value.code is PublicErrorCode.LIMIT_EXCEEDED


async def test_attached_child_can_continue_after_parent_work_closes(
    tmp_path: Path,
) -> None:
    """Parent completion does not revoke an already attached child's independent route."""

    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        parent = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Detached parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "detached-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        delegate_request = StartRequest.model_validate(
            {
                **_identity(),
                "mode": "delegate",
                "task_title": "Detached child",
                "session_id": parent.session_id,
                "requested_view": "compact",
            }
        )
        delegated = await app.start(delegate_request, repository_privacy_context=_REPOSITORY)
        attached = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "attach",
                    "task_title": "Detached child",
                    "attach_handle": delegated.as_wire()["attach_handle"],
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        current_parent = await app.start(delegate_request, repository_privacy_context=_REPOSITORY)
        await app.publish_work(
            PublishWorkRequest.model_validate(
                {
                    **_identity(),
                    "session_id": parent.session_id,
                    "writer_id": parent.writer_id,
                    "expected_frontier": current_parent.frontier.model_dump(mode="json"),
                    "event_drafts": [
                        {
                            "event_id": new_id(IdKind.EVENT),
                            "schema": {"name": "work_closed", "version": "1.0.0"},
                            "occurred_at": "2026-09-05T12:00:00.000Z",
                            "causal_parents": [],
                            "payload": {},
                            "artifact_refs": [],
                            "evidence_refs": [],
                        }
                    ],
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        child_action = await app.publish_work(
            PublishWorkRequest.model_validate(
                {
                    **_identity(),
                    "session_id": attached.session_id,
                    "writer_id": attached.writer_id,
                    "expected_frontier": attached.frontier.model_dump(mode="json"),
                    "event_drafts": [
                        {
                            "event_id": new_id(IdKind.EVENT),
                            "schema": {"name": "action_recorded", "version": "1.0.0"},
                            "occurred_at": "2026-09-05T12:00:00.000Z",
                            "causal_parents": [],
                            "payload": {
                                "action_id": new_id(IdKind.ACTION),
                                "action_kind": "review",
                                "description": "Detached child continues",
                            },
                            "artifact_refs": [],
                            "evidence_refs": [],
                        }
                    ],
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert isinstance(child_action, PublishWorkInternalResult)
        assert child_action.task_id == attached.task_id
