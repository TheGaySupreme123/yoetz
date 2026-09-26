"""Focused real SQLite proof for pending self-registration admission."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from builders.multi_agent import multi_agent_service
from yoetz.ports.control import RepositoryPrivacyContext
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import PublishWorkRequest, StartRequest

pytestmark = pytest.mark.anyio

_REPOSITORY = RepositoryPrivacyContext("hmac-sha256:" + "f" * 64, "git_common_root")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _workspace(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
    return root.resolve()


def _identity() -> dict[str, object]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": new_id(IdKind.REQUEST),
        "actor": {"actor_id": "harness:self-registration", "actor_type": "harness"},
        "client": {
            "kind": "cooperative_agent",
            "version": "0.1.0",
            "integration": "cooperative_mcp",
        },
    }


async def test_real_sqlite_self_registration_keeps_pending_origin(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        parent = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Self-registration parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "self-registration-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        request = StartRequest.model_validate(
            {
                **_identity(),
                "mode": "create",
                "task_title": "Self-registered pending child",
                "workspace_ref": str(workspace),
                "external_ref": "self-registration-child",
                "parent_session_id": parent.session_id,
                "requested_view": "compact",
            }
        )
        child = await service.app.start(
            request,
            repository_privacy_context=_REPOSITORY,
        )
        replay = await service.app.start(request, repository_privacy_context=_REPOSITORY)
        assert replay.task_id == child.task_id
        assert replay.session_id == child.session_id
        assert replay.acceptance == "pending"
        assert child.acceptance == "pending"
        lineage = await service.app.start_catalog.task_lineage(child.task_id)
        assert lineage is not None
        assert lineage.parent_task_id == parent.task_id
        assert lineage.origin is not None and lineage.origin.value == "self_registered"
        assert lineage.acceptance is not None and lineage.acceptance.value == "pending"


async def test_real_sqlite_rejects_historical_and_closed_parent_admission(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        parent = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Historical parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "historical-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        rotated = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create_or_attach",
                    "task_title": "Historical parent resumed",
                    "workspace_ref": str(workspace),
                    "external_ref": "historical-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        assert rotated.task_id == parent.task_id
        assert rotated.session_id != parent.session_id

        for mode in ("delegate", "create"):
            request_body: dict[str, object] = {
                **_identity(),
                "mode": mode,
                "task_title": f"Historical child {mode}",
                "requested_view": "compact",
            }
            if mode == "delegate":
                request_body["session_id"] = parent.session_id
            else:
                request_body["parent_session_id"] = parent.session_id
                request_body["workspace_ref"] = str(workspace)
                request_body["external_ref"] = "historical-self-registration"
            with pytest.raises(PublicOperationError) as caught:
                await service.app.start(
                    StartRequest.model_validate(request_body),
                    repository_privacy_context=_REPOSITORY,
                )
            # A delegated selector is rejected by the start catalog before the lineage
            # coordinator can inspect the retired route.  Self-registration reaches the
            # coordinator and receives its typed active-session refusal.
            expected = (
                PublicErrorCode.SESSION_NOT_FOUND
                if mode == "delegate"
                else PublicErrorCode.SESSION_CONFLICT
            )
            assert caught.value.code is expected, mode

        closed = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Closed parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "closed-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        close_request = PublishWorkRequest.model_validate(
            {
                **_identity(),
                "session_id": closed.session_id,
                "writer_id": closed.writer_id,
                "expected_frontier": closed.frontier.model_dump(mode="json"),
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
        )
        await service.app.publish_work(close_request, repository_privacy_context=_REPOSITORY)

        for mode in ("delegate", "create"):
            request_body = {
                **_identity(),
                "mode": mode,
                "task_title": f"Closed child {mode}",
                "requested_view": "compact",
            }
            if mode == "delegate":
                request_body["session_id"] = closed.session_id
            else:
                request_body["parent_session_id"] = closed.session_id
                request_body["workspace_ref"] = str(workspace)
                request_body["external_ref"] = "closed-self-registration"
            with pytest.raises(PublicOperationError) as caught:
                await service.app.start(
                    StartRequest.model_validate(request_body),
                    repository_privacy_context=_REPOSITORY,
                )
            assert caught.value.code is PublicErrorCode.SESSION_CONFLICT
