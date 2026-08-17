"""Codex lifecycle hook handler unit tests."""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from yoetz.adapters.integrations.codex_lifecycle import (
    load_mapping,
    mapping_from_start_ids,
    store_mapping,
)
from yoetz.cli import hooks as hooks_module
from yoetz.cli.hooks import (
    INACTIVE_CONTEXT,
    handle_post_tool_use,
    handle_session_start,
    handle_user_prompt_submit,
    intake_cue_text,
)
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.errors import PublicErrorCode
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import OperationFailureModel


def _task_ids() -> tuple[str, str, str]:
    return new_id(IdKind.TASK), new_id(IdKind.SESSION), new_id(IdKind.WRITER)


def test_user_prompt_submit_emits_intake_cue_without_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    guidance = tmp_path / "guidance"
    guidance.mkdir()
    text = (
        "# When to use Yoetz\n\n"
        "Use Yoetz for material multi-step, delegated, resumable, or verification-heavy work. "
        "Call `start` before substantive work.\n\n"
        "# What Yoetz is\n\n"
        "Yoetz is a local work ledger.\n"
    )
    (guidance / "agent-instructions.md").write_text(text, encoding="utf-8")
    connects: list[object] = []

    async def forbidden_connect(_kind: object) -> object:
        connects.append(_kind)
        raise AssertionError("must not connect")

    monkeypatch.setattr("yoetz.cli.hooks.connect_service", forbidden_connect)
    stdout = io.BytesIO()
    code = handle_user_prompt_submit(
        stdin_bytes=b'{"session_id":"s1","hook_event_name":"UserPromptSubmit"}',
        stdout=stdout,
        resource_root=tmp_path,
        _state=tmp_path,
    )
    assert code == 0
    assert connects == []
    payload = json.loads(stdout.getvalue().decode("utf-8"))
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Call `start` before substantive work" in context
    assert intake_cue_text(resource_root=tmp_path) in context or context in intake_cue_text(
        resource_root=tmp_path
    )


def test_post_tool_use_successful_start_creates_mapping(tmp_path: Path) -> None:
    task_id, session_id, writer_id = _task_ids()
    codex_session = "codex-post-1"
    tool_response = {
        "structuredContent": {
            "ok": True,
            "task_id": task_id,
            "session_id": session_id,
            "writer_id": writer_id,
            "frontier": {"sequence": "0", "head_digest": "genesis"},
        }
    }
    payload = {
        "session_id": codex_session,
        "tool_name": "mcp__yoetz__start",
        "tool_response": tool_response,
    }
    stdout = io.BytesIO()
    code = handle_post_tool_use(
        stdin_bytes=json.dumps(payload).encode("utf-8"),
        stdout=stdout,
        _state=tmp_path,
    )
    assert code == 0
    mapping = load_mapping(codex_session, _state=tmp_path)
    assert mapping is not None
    assert mapping.yoetz_task_id == task_id
    assert mapping.yoetz_session_id == session_id
    assert mapping.yoetz_writer_id == writer_id
    assert mapping.last_frontier == "0:genesis"


def test_post_tool_use_failed_start_creates_none(tmp_path: Path) -> None:
    payload = {
        "session_id": "codex-fail",
        "tool_name": "mcp__yoetz__start",
        "tool_response": {"structuredContent": {"ok": False}},
    }
    code = handle_post_tool_use(
        stdin_bytes=json.dumps(payload).encode("utf-8"),
        stdout=io.BytesIO(),
        _state=tmp_path,
    )
    assert code == 0
    assert load_mapping("codex-fail", _state=tmp_path) is None


def test_post_tool_use_non_start_creates_none(tmp_path: Path) -> None:
    task_id, session_id, writer_id = _task_ids()
    payload = {
        "session_id": "codex-other",
        "tool_name": "mcp__yoetz__status",
        "tool_response": {
            "structuredContent": {
                "ok": True,
                "task_id": task_id,
                "session_id": session_id,
                "writer_id": writer_id,
            }
        },
    }
    handle_post_tool_use(
        stdin_bytes=json.dumps(payload).encode("utf-8"),
        stdout=io.BytesIO(),
        _state=tmp_path,
    )
    assert load_mapping("codex-other", _state=tmp_path) is None


def test_post_tool_use_duplicate_idempotent(tmp_path: Path) -> None:
    task_id, session_id, writer_id = _task_ids()
    payload = {
        "session_id": "codex-dup",
        "tool_name": "start",
        "tool_response": {
            "ok": True,
            "task_id": task_id,
            "session_id": session_id,
            "writer_id": writer_id,
            "frontier": {"sequence": "1", "head_digest": "sha256:" + "a" * 64},
        },
    }
    raw = json.dumps(payload).encode("utf-8")
    handle_post_tool_use(stdin_bytes=raw, stdout=io.BytesIO(), _state=tmp_path)
    handle_post_tool_use(stdin_bytes=raw, stdout=io.BytesIO(), _state=tmp_path)
    mapping = load_mapping("codex-dup", _state=tmp_path)
    assert mapping is not None
    assert mapping.yoetz_task_id == task_id


def test_post_tool_use_malformed_json_no_traceback(tmp_path: Path) -> None:
    code = handle_post_tool_use(
        stdin_bytes=b"{not-json",
        stdout=io.BytesIO(),
        _state=tmp_path,
    )
    assert code == 0
    assert (
        list((tmp_path / "codex-lifecycle").glob("*.json")) == []
        if (tmp_path / "codex-lifecycle").exists()
        else True
    )


def test_session_start_no_mapping_inactive(tmp_path: Path) -> None:
    stdout = io.BytesIO()
    code = handle_session_start(
        stdin_bytes=json.dumps(
            {"session_id": "missing-map", "source": "resume", "hook_event_name": "SessionStart"}
        ).encode(),
        stdout=stdout,
        _state=tmp_path,
    )
    assert code == 0
    payload = json.loads(stdout.getvalue().decode("utf-8"))
    assert payload["hookSpecificOutput"]["additionalContext"] == INACTIVE_CONTEXT
    assert "tsk_" not in payload["hookSpecificOutput"]["additionalContext"]


class _FakeStatusResult:
    def __init__(self, branch: object) -> None:
        self.root = branch


class _FakeSuccess:
    def __init__(self, task_id: str, session_id: str, writer_id: str) -> None:
        self.task_id = task_id
        self.session_id = session_id
        self.writer_id = writer_id
        self.head_frontier = type("F", (), {"sequence": "3", "head_digest": "sha256:" + "c" * 64})()


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.task_id = ""
        self.session_id = ""
        self.writer_id = ""

    async def status(self, request: object, *, deadline_ms: int | None = None) -> object:
        self.calls.append("status")
        assert deadline_ms is not None
        return _FakeStatusResult(_FakeSuccess(self.task_id, self.session_id, self.writer_id))

    async def start(self, *_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("must not create ledger events")

    async def close(self) -> None:
        return None


def test_session_start_active_with_fake_service(tmp_path: Path) -> None:
    task_id, session_id, writer_id = _task_ids()
    store_mapping(
        mapping_from_start_ids(
            codex_session_id="codex-active",
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier="0:genesis",
        ),
        _state=tmp_path,
    )
    client = _FakeClient()
    client.task_id = task_id
    client.session_id = session_id
    client.writer_id = writer_id

    async def connect(_kind: ControlClientKind) -> _FakeClient:
        return client

    stdout = io.BytesIO()
    code = handle_session_start(
        stdin_bytes=json.dumps({"session_id": "codex-active", "source": "compact"}).encode(),
        stdout=stdout,
        _state=tmp_path,
        connect=connect,
    )
    assert code == 0
    assert client.calls == ["status"]
    text = json.loads(stdout.getvalue().decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    assert task_id in text
    assert "Call status before further material work" in text
    assert "3:sha256:" in text


def test_session_start_unreachable_service(tmp_path: Path) -> None:
    task_id, session_id, writer_id = _task_ids()
    store_mapping(
        mapping_from_start_ids(
            codex_session_id="codex-down",
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier=None,
        ),
        _state=tmp_path,
    )

    async def connect(_kind: ControlClientKind) -> object:
        raise ControlError("service_unavailable", retryable=True)

    stdout = io.BytesIO()
    code = handle_session_start(
        stdin_bytes=json.dumps({"session_id": "codex-down", "source": "resume"}).encode(),
        stdout=stdout,
        _state=tmp_path,
        connect=connect,  # pyright: ignore[reportArgumentType]
    )
    assert code == 0
    text = json.loads(stdout.getvalue().decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    assert text == hooks_module._UNAVAILABLE_CONTEXT  # pyright: ignore[reportPrivateUsage]


def _failure_result(code: PublicErrorCode, *, retryable: bool = False) -> _FakeStatusResult:
    return _FakeStatusResult(
        OperationFailureModel.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "ok": False,
                "error": {
                    "code": code.value,
                    "message": "The requested task attachment conflicts.",
                    "retryable": retryable,
                    "correlation_id": f"err_{uuid.uuid4()}",
                },
            }
        )
    )


def _session_start_context_for_failure(
    tmp_path: Path,
    codex_session_id: str,
    result: _FakeStatusResult | Exception,
) -> str:
    task_id, session_id, writer_id = _task_ids()
    store_mapping(
        mapping_from_start_ids(
            codex_session_id=codex_session_id,
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier="0:genesis",
        ),
        _state=tmp_path,
    )

    class _Client:
        async def status(self, request: object, *, deadline_ms: int | None = None) -> object:
            del request, deadline_ms
            if isinstance(result, Exception):
                raise result
            return result

        async def close(self) -> None:
            return None

    async def connect(_kind: ControlClientKind) -> _Client:
        return _Client()

    stdout = io.BytesIO()
    code = handle_session_start(
        stdin_bytes=json.dumps({"session_id": codex_session_id, "source": "resume"}).encode(),
        stdout=stdout,
        _state=tmp_path,
        connect=connect,  # pyright: ignore[reportArgumentType]
    )
    assert code == 0
    context = json.loads(stdout.getvalue().decode("utf-8"))["hookSpecificOutput"][
        "additionalContext"
    ]
    assert type(context) is str
    return context


@pytest.mark.parametrize(
    "code", [PublicErrorCode.SESSION_CONFLICT, PublicErrorCode.SESSION_NOT_FOUND]
)
def test_session_start_stale_mapping_is_not_reported_unavailable(
    tmp_path: Path, code: PublicErrorCode
) -> None:
    """SESSION_* means the mapping is stale, not that the service is down (#308)."""

    text = _session_start_context_for_failure(tmp_path, "codex-stale", _failure_result(code))
    assert text == hooks_module._STALE_MAPPING_CONTEXT  # pyright: ignore[reportPrivateUsage]
    assert "unavailable" not in text
    assert "start" in text
    # The mapping is kept: repair flows through the agent's own re-start via
    # handle_post_tool_use, never through hook-side guessing.
    assert load_mapping("codex-stale", _state=tmp_path) is not None
    diagnostics = (tmp_path / "observation" / "hook-diagnostics.jsonl").read_text()
    assert diagnostics.count('"reason":"mapping_stale"') == 1


def test_session_start_vault_locked_failure_is_locked(tmp_path: Path) -> None:
    text = _session_start_context_for_failure(
        tmp_path, "codex-locked", _failure_result(PublicErrorCode.VAULT_LOCKED, retryable=True)
    )
    assert text == hooks_module._LOCKED_CONTEXT  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize(
    "code",
    [
        PublicErrorCode.BUNDLE_BUSY,
        PublicErrorCode.OPERATION_PENDING,
        PublicErrorCode.FRONTIER_CONFLICT,
    ],
)
def test_session_start_transient_failure_is_retry(tmp_path: Path, code: PublicErrorCode) -> None:
    text = _session_start_context_for_failure(
        tmp_path, "codex-busy", _failure_result(code, retryable=True)
    )
    assert text == hooks_module._RETRY_CONTEXT  # pyright: ignore[reportPrivateUsage]
    assert "unavailable" not in text


def test_session_start_degraded_service_stays_unavailable(tmp_path: Path) -> None:
    text = _session_start_context_for_failure(
        tmp_path,
        "codex-degraded",
        _failure_result(PublicErrorCode.SERVICE_UNAVAILABLE, retryable=True),
    )
    assert text == hooks_module._UNAVAILABLE_CONTEXT  # pyright: ignore[reportPrivateUsage]


def test_session_start_privacy_failure_names_the_ceremony(tmp_path: Path) -> None:
    text = _session_start_context_for_failure(
        tmp_path,
        "codex-privacy",
        _failure_result(PublicErrorCode.PRIVACY_AUTHORITY_REQUIRED),
    )
    assert text == hooks_module._PRIVACY_CONTEXT  # pyright: ignore[reportPrivateUsage]


def test_session_start_control_timeout_is_retry(tmp_path: Path) -> None:
    text = _session_start_context_for_failure(
        tmp_path, "codex-timeout", ControlError("request_timeout", retryable=True)
    )
    assert text == hooks_module._RETRY_CONTEXT  # pyright: ignore[reportPrivateUsage]


def test_status_error_class_table_is_exhaustive() -> None:
    """A new PublicErrorCode member must be classified, never silently collapsed."""

    table = hooks_module._STATUS_ERROR_CLASSES  # pyright: ignore[reportPrivateUsage]
    assert set(table) == set(PublicErrorCode)
    assert set(table.values()) <= {"stale", "locked", "retry", "privacy", "unavailable"}


def test_control_error_class_table_is_exhaustive() -> None:
    from yoetz.ports.control import (
        _CONTROL_ERROR_REASONS,  # pyright: ignore[reportPrivateUsage]
    )

    table = hooks_module._CONTROL_ERROR_CLASSES  # pyright: ignore[reportPrivateUsage]
    assert set(table) == _CONTROL_ERROR_REASONS
    assert set(table.values()) <= {"locked", "retry", "privacy", "unavailable"}


def test_session_start_clear_removes_mapping(tmp_path: Path) -> None:
    task_id, session_id, writer_id = _task_ids()
    store_mapping(
        mapping_from_start_ids(
            codex_session_id="codex-clear",
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier=None,
        ),
        _state=tmp_path,
    )
    code = handle_session_start(
        stdin_bytes=json.dumps({"session_id": "codex-clear", "source": "clear"}).encode(),
        stdout=io.BytesIO(),
        _state=tmp_path,
    )
    assert code == 0
    assert load_mapping("codex-clear", _state=tmp_path) is None


def test_session_start_startup_is_noop(tmp_path: Path) -> None:
    stdout = io.BytesIO()
    code = handle_session_start(
        stdin_bytes=json.dumps({"session_id": "codex-boot", "source": "startup"}).encode(),
        stdout=stdout,
        _state=tmp_path,
    )
    assert code == 0
    assert (
        stdout.getvalue().strip() in {b"{}", b""}
        or json.loads(stdout.getvalue().decode("utf-8")) == {}
    )
