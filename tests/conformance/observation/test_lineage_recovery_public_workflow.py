"""Crash-window conformance for durable lineage lifecycle reconciliation."""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import yoetz.application.start as start_module
from builders.multi_agent import multi_agent_service
from yoetz.application.lineage import DelegationOperationState, DelegationPhase
from yoetz.application.service import Application
from yoetz.domain.coordination import WorkState
from yoetz.ports.control import RepositoryPrivacyContext
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import PublishWorkRequest, StartRequest

pytestmark = pytest.mark.anyio

_REPOSITORY = RepositoryPrivacyContext("hmac-sha256:" + "d" * 64, "git_common_root")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _identity() -> dict[str, object]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": new_id(IdKind.REQUEST),
        "actor": {"actor_id": "harness:recovery", "actor_type": "harness"},
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


async def test_append_before_lineage_sync_is_reconciled_on_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        started = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Crash window",
                    "workspace_ref": str(workspace),
                    "external_ref": "crash-window",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        before = await app.start_catalog.task_lineage(started.task_id)
        assert before is not None and before.work_state is WorkState.OPEN

        async def fail_after_append(
            _app: object, _request: object, _payloads: tuple[object, ...]
        ) -> None:
            raise RuntimeError("simulated_crash_after_ledger_append")

        original = Application._sync_lineage_publication  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(Application, "_sync_lineage_publication", fail_after_append)
        with pytest.raises(RuntimeError, match="simulated_crash_after_ledger_append"):
            await app.publish_work(
                PublishWorkRequest.model_validate(
                    {
                        **_identity(),
                        "session_id": started.session_id,
                        "writer_id": started.writer_id,
                        "expected_frontier": started.frontier.model_dump(mode="json"),
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
        monkeypatch.setattr(Application, "_sync_lineage_publication", original)

        stale = await app.start_catalog.task_lineage(started.task_id)
        assert stale is not None and stale.work_state is WorkState.OPEN

        # A public child admission races the append-before-sync crash window.  START must repair
        # the accepted ledger event while holding the publication lock, then refuse the now
        # terminal parent instead of minting from the stale OPEN catalog projection.
        with pytest.raises(PublicOperationError) as closed_parent:
            await app.start(
                StartRequest.model_validate(
                    {
                        **_identity(),
                        "mode": "create",
                        "task_title": "Child after crashed close",
                        "workspace_ref": str(workspace),
                        "external_ref": "child-after-crashed-close",
                        "parent_session_id": started.session_id,
                        "requested_view": "compact",
                    }
                ),
                repository_privacy_context=_REPOSITORY,
            )
        assert closed_parent.value.code is PublicErrorCode.SESSION_CONFLICT

        await app.recover_lineage()
        repaired = await app.start_catalog.task_lineage(started.task_id)
        assert repaired is not None and repaired.work_state is WorkState.CLOSED

        # A periodic/restart retry replays the same durable event without a second transition.
        await app.recover_lineage()
        replayed = await app.start_catalog.task_lineage(started.task_id)
        assert replayed is not None and replayed.work_state is WorkState.CLOSED


async def test_reclaimed_delegation_finishes_after_parent_event_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    async with multi_agent_service(tmp_path / "state") as service:
        app = service.app
        parent = await app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Delegation parent",
                    "workspace_ref": str(workspace),
                    "external_ref": "delegation-parent",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        request = StartRequest.model_validate(
            {
                **_identity(),
                "mode": "delegate",
                "task_title": "Delegation child",
                "session_id": parent.session_id,
                "requested_view": "compact",
            }
        )
        original = start_module._append_delegation_event  # pyright: ignore[reportPrivateUsage]

        async def fail_once(_app: object, _operation: object) -> None:
            raise RuntimeError("simulated_crash_after_child_bundle")

        monkeypatch.setattr(start_module, "_append_delegation_event", fail_once)
        with pytest.raises(RuntimeError, match="simulated_crash_after_child_bundle"):
            await app.start(request, repository_privacy_context=_REPOSITORY)
        monkeypatch.setattr(start_module, "_append_delegation_event", original)

        lineage = app.lineage
        assert lineage is not None
        operation = await lineage.store.get_operation(request.request_id)
        assert operation is not None
        assert operation.phase is DelegationPhase.CHILD_BUNDLE_READY
        assert operation.state is DelegationOperationState.PENDING

        # The ready maintenance recovery path reclaims and completes the same operation. It does
        # not mint another child or require a second parent request.
        await lineage.store.save_operation(
            replace(operation, lease_expires_at=service.clock.now_utc())
        )
        await app.recover_lineage()
        recovered = await lineage.store.get_operation(request.request_id)
        assert recovered is not None
        assert recovered.phase is DelegationPhase.TERMINAL
        assert recovered.state is DelegationOperationState.COMPLETE
        replayed = await app.start(request, repository_privacy_context=_REPOSITORY)
        assert replayed.task_id == recovered.child_task_id
