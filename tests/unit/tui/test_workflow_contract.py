"""Terminal workflows must reach the service through valid public requests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from yoetz.protocol.models import (
    CheckAwaitingHumanModel,
    CheckRequestModel,
    ReceiptRequestModel,
    StartRequestModel,
)
from yoetz.tui.models import CheckMode
from yoetz.tui.runtime import RuntimeError_, YoetzRuntime

pytestmark = pytest.mark.anyio
_SESSION = "ses_52000000-0000-4000-8000-000000000001"
_SUCCESSOR = "ses_52000000-0000-4000-8000-000000000002"
_TASK = "tsk_52000000-0000-4000-8000-000000000001"
_WRITER = "wri_52000000-0000-4000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Client:
    def __init__(self) -> None:
        self.requests: list[StartRequestModel | CheckRequestModel | ReceiptRequestModel] = []

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

    @staticmethod
    def _refused() -> object:
        return SimpleNamespace(
            root=SimpleNamespace(
                ok=False,
                error=SimpleNamespace(code="BUNDLE_BUSY", message="The task is busy."),
            )
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
