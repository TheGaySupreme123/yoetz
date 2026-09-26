"""Terminal workflows must reach the service through valid public requests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from yoetz.cli.render import render_human_status
from yoetz.protocol.models import (
    CheckAwaitingHumanModel,
    CheckRequestModel,
    ReceiptRequestModel,
    StartRequestModel,
    StatusRequestModel,
    StatusResultModel,
    StatusSuccessModel,
)
from yoetz.tui.models import CheckMode
from yoetz.tui.runtime import RuntimeError_, YoetzRuntime

pytestmark = pytest.mark.anyio
_SESSION = "ses_52000000-0000-4000-8000-000000000001"
_SUCCESSOR = "ses_52000000-0000-4000-8000-000000000002"
_TASK = "tsk_52000000-0000-4000-8000-000000000001"
_WRITER = "wri_52000000-0000-4000-8000-000000000001"
_LINEAGE_TASK = "tsk_52000000-0000-4000-8000-000000000010"
_LINEAGE_CHILD = "tsk_52000000-0000-4000-8000-000000000011"
_LINEAGE_REQUEST = "req_52000000-0000-4000-8000-000000000012"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Client:
    def __init__(self) -> None:
        self.requests: list[
            StartRequestModel | CheckRequestModel | ReceiptRequestModel | StatusRequestModel
        ] = []

    async def start(self, request: StartRequestModel) -> object:
        self.requests.append(request)
        return SimpleNamespace(
            root=SimpleNamespace(
                ok=True,
                task_id=_TASK,
                session_id=_SUCCESSOR,
                writer_id=_WRITER,
                frontier=SimpleNamespace(sequence="7", head_digest="sha256:" + "a" * 64),
                compact=SimpleNamespace(),
            )
        )

    async def check(self, request: CheckRequestModel) -> object:
        self.requests.append(request)
        return self._refused()

    async def receipt(self, request: ReceiptRequestModel) -> object:
        self.requests.append(request)
        return self._refused()

    async def status(self, request: StatusRequestModel) -> object:
        self.requests.append(request)
        return _lineage_status_result()

    @staticmethod
    def _refused() -> object:
        return SimpleNamespace(
            root=SimpleNamespace(
                ok=False,
                error=SimpleNamespace(code="BUNDLE_BUSY", message="The task is busy."),
            )
        )


def _lineage_status_result() -> StatusResultModel:
    digest = "sha256:" + "a" * 64
    return StatusResultModel.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": _LINEAGE_REQUEST,
            "ok": True,
            "task_id": _LINEAGE_TASK,
            "session_id": _SUCCESSOR,
            "writer_id": _WRITER,
            "view": "lineage",
            "requested_frontier": {"sequence": "7", "head_digest": digest},
            "head_frontier": {"sequence": "8", "head_digest": digest},
            "subject_frontier": {"sequence": "8", "head_digest": digest},
            "result_frontier": {"sequence": "8", "head_digest": digest},
            "projection_lag": "0",
            "projection_version": "0.1.0",
            "rebuild_state": "current",
            "page": {
                "parent_task_id": None,
                "children": [
                    {
                        "task_id": _LINEAGE_CHILD,
                        "parent_task_id": _LINEAGE_TASK,
                        "origin": "parent_minted",
                        "acceptance": "accepted",
                        "work_state": "open",
                        "session_health": "active",
                        "depth": "1",
                        "rollup_state": "open_gap",
                        "blocking_conditions": ["lineage_child_incomplete"],
                    }
                ],
                "annotations": [
                    {
                        "correlation_id": "claude-child-1",
                        "subagent_id": "claude-child-1",
                        "parent_tool_call_id": None,
                        "origin": "host_observed",
                        "acceptance": "pending",
                    }
                ],
                "next_cursor": None,
            },
            "coverage": {
                "publication_channels": ["cooperative_mcp"],
                "authorship_assurance": "self_asserted",
                "artifact_observation": "published_only",
                "evidence_immutability": "content_digest",
                "ledger_freshness": "current",
                "check_types": ["none"],
                "known_gaps": [],
            },
            "gaps": [],
            "import_status": {
                "pending_count": "0",
                "terminal_count": "0",
                "phase": None,
                "report_evidence_id": None,
                "source_identity_digest": None,
            },
            "closure_readiness": {
                "declared_obligation_count": "0",
                "no_obligations_reason": None,
                "open_obligation_count": "0",
                "unanswered_finding_count": "0",
                "receipt_blocking_finding_count": "0",
                "blocking_conditions": ["no_obligations_declared"],
            },
            "privacy_projection": {
                "sink": "local_human_view",
                "local_disclosure_receipt_id": "egr_52000000-0000-4000-8000-000000000013",
                "policy_id": "pvy_52000000-0000-4000-8000-000000000014",
                "policy_version": "1",
                "policy_digest": digest,
                "included_categories": [],
                "blocked_categories": [],
                "omitted_pointers": [],
                "projection_commitment": "hmac-sha256:" + "b" * 64,
            },
        }
    )


async def test_exact_selector_and_returned_frontier_reach_check_and_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _Client()

    @asynccontextmanager
    async def connect(_runtime: YoetzRuntime) -> AsyncGenerator[_Client]:
        yield client

    monkeypatch.setattr(YoetzRuntime, "_client", connect)
    runtime = YoetzRuntime(cwd=tmp_path)
    detail = await runtime.open_task(_SESSION)
    assert detail.item.subject_id == _TASK
    start = client.requests[0]
    assert isinstance(start, StartRequestModel)
    assert start.session_id == _SESSION
    assert start.mode == "attach"
    with pytest.raises(RuntimeError_, match="The task is busy"):
        await runtime.run_check(_SESSION, CheckMode.DETERMINISTIC_ONLY)
    with pytest.raises(RuntimeError_, match="The task is busy"):
        await runtime.build_receipt(_SESSION, "markdown")
    for request in client.requests[1:]:
        assert isinstance(request, (CheckRequestModel, ReceiptRequestModel))
        assert request.session_id == _SUCCESSOR
        assert request.writer_id == _WRITER
        assert request.expected_frontier.sequence == "7"
        assert request.expected_frontier.head_digest == "sha256:" + "a" * 64
        assert request.actor.actor_id == "yoetz:tui"
    receipt = client.requests[-1]
    assert isinstance(receipt, ReceiptRequestModel)
    assert receipt.task_id == _TASK
    assert receipt.redaction_profile.value == "default_local_export"


async def test_lineage_view_reuses_cli_renderer_and_preserves_child_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _Client()

    @asynccontextmanager
    async def connect(_runtime: YoetzRuntime) -> AsyncGenerator[_Client]:
        yield client

    monkeypatch.setattr(YoetzRuntime, "_client", connect)
    runtime = YoetzRuntime(cwd=tmp_path)
    await runtime.open_task(_SESSION)

    rendered = await runtime.task_status(_SESSION, "lineage")
    result = _lineage_status_result().root
    assert isinstance(result, StatusSuccessModel)
    expected = tuple(render_human_status(result).splitlines())

    assert rendered.lines == expected
    assert (
        f"- {_LINEAGE_CHILD}: parent_minted, accepted; work open, session active; rollup open_gap"
    ) in rendered.lines
    assert "  Completion gaps: lineage_child_incomplete" in rendered.lines
    assert "- Observed subagent claude-child-1: pending child binding" in rendered.lines

    status_request = client.requests[-1]
    assert isinstance(status_request, StatusRequestModel)
    assert status_request.session_id == _SUCCESSOR
    assert status_request.writer_id == _WRITER
    assert status_request.view == "lineage"
    assert status_request.limit == "50"
    assert status_request.cursor is None


async def test_title_cannot_select_another_task(tmp_path: Path) -> None:
    runtime = YoetzRuntime(cwd=tmp_path)
    with pytest.raises(RuntimeError_, match="session ID"):
        await runtime.open_task("An unrelated task title")


async def test_awaiting_human_replays_the_exact_request_even_if_picker_mode_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class AwaitingClient(_Client):
        async def check(self, request: CheckRequestModel) -> object:
            self.requests.append(request)
            if len(self.requests) > 2:
                return self._refused()
            frontier = request.expected_frontier.model_dump(mode="json")
            return SimpleNamespace(
                root=CheckAwaitingHumanModel.model_validate(
                    {
                        "protocol_version": "0.1",
                        "schema_version": "1.0.0",
                        "request_id": request.request_id,
                        "ok": True,
                        "state": "awaiting_human",
                        "task_id": _TASK,
                        "session_id": _SUCCESSOR,
                        "writer_id": _WRITER,
                        "subject_frontier": frontier,
                        "result_frontier": frontier,
                        "semantic_status": "awaiting_human",
                        "semantic_reason": "human_approval_required",
                        "continuation": {
                            "kind": "repository_privacy_setup",
                            "command": ["yoetz", "--privacy"],
                            "replay_request_id": request.request_id,
                            "instruction": "Complete the trusted review, then replay this request.",
                        },
                        "privacy_projection": {
                            "sink": "local_human_view",
                            "local_disclosure_receipt_id": "egr_52000000-0000-4000-8000-000000000001",
                            "policy_id": "pvy_52000000-0000-4000-8000-000000000001",
                            "policy_version": "1",
                            "policy_digest": "sha256:" + "b" * 64,
                            "included_categories": [],
                            "blocked_categories": [],
                            "omitted_pointers": [],
                            "projection_commitment": "hmac-sha256:" + "c" * 64,
                        },
                        "versions": {
                            "protocol_version": "0.1",
                            "engine_version": "0.1.0",
                            "projection_version": "yoetz/0.1.0",
                            "policy_packs": ["research-evidence/0.1.0", "work-integrity/0.1.0"],
                        },
                    }
                )
            )

    client = AwaitingClient()

    @asynccontextmanager
    async def connect(_runtime: YoetzRuntime) -> AsyncGenerator[AwaitingClient]:
        yield client

    monkeypatch.setattr(YoetzRuntime, "_client", connect)
    runtime = YoetzRuntime(cwd=tmp_path)
    await runtime.open_task(_SESSION)
    state, lines = await runtime.run_check(_SESSION, CheckMode.SEMANTIC_REQUIRED)
    assert state == "awaiting_human"
    assert "yoetz --privacy" in "\n".join(lines)
    assert "No verdict yet" in "\n".join(lines)
    with pytest.raises(RuntimeError_, match="The task is busy"):
        await runtime.run_check(_SESSION, CheckMode.DETERMINISTIC_ONLY)
    assert client.requests[1] is client.requests[2]
    assert isinstance(client.requests[1], CheckRequestModel)
    assert client.requests[1].mode == "semantic_required"
