"""Codex lifecycle hook handler unit tests."""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from yoetz.adapters.integrations.codex_lifecycle import (
    load_mapping,
    mapping_from_start_ids,
    store_mapping,
)
from yoetz.cli import app as app_module
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


def test_user_prompt_submit_cli_forwards_the_explicit_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def operation(name: str):
        assert name == "handle_user_prompt_submit"

        def handle(**kwargs: object) -> int:
            captured.update(kwargs)
            return 0

        return handle

    monkeypatch.setattr(app_module, "_hooks_operation", operation)
    result = CliRunner().invoke(
        app_module.app,
        ["hooks", "user-prompt-submit", "--workspace", "."],
    )

    assert result.exit_code == 0
    assert captured == {"workspace": "."}


def test_session_start_cli_forwards_the_explicit_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The resume/compact status read needs the workspace the daemon fences on (#578)."""

    captured: dict[str, object] = {}

    def operation(name: str):
        assert name == "handle_session_start"

        def handle(**kwargs: object) -> int:
            captured.update(kwargs)
            return 0

        return handle

    monkeypatch.setattr(app_module, "_hooks_operation", operation)
    result = CliRunner().invoke(app_module.app, ["hooks", "session-start", "--workspace", "."])

    assert result.exit_code == 0
    assert captured == {"workspace": "."}


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
    # The context carries a selector guidance recognises (#580): the mapped
    # session/writer for `status`, and `mode=attach` by session id to continue.
    assert session_id in text
    assert writer_id in text
    assert "Call status with these ids before further material work" in text
    assert f"mode=attach and session_id {session_id}" in text
    assert "create_or_attach" in text
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


def _failure_result(
    code: PublicErrorCode,
    *,
    retryable: bool = False,
    safe_details: dict[str, object] | None = None,
) -> _FakeStatusResult:
    error: dict[str, object] = {
        "code": code.value,
        "message": "The requested task attachment conflicts.",
        "retryable": retryable,
        "correlation_id": f"err_{uuid.uuid4()}",
    }
    if safe_details is not None:
        error["safe_details"] = safe_details
    return _FakeStatusResult(
        OperationFailureModel.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "ok": False,
                "error": error,
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
    mapping = load_mapping("codex-stale", _state=tmp_path)
    assert mapping is not None
    assert text == hooks_module._stale_mapping_context(mapping)  # pyright: ignore[reportPrivateUsage]
    assert mapping.yoetz_session_id in text
    assert "mode=attach" in text
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
    assert set(table.values()) <= {
        "stale",
        "locked",
        "retry",
        "privacy",
        "storage_unsafe",
        "storage_corrupt",
        "unavailable",
    }
    # The two storage codes prescribe opposite next steps and must never share
    # the generic class again (#338).
    assert table[PublicErrorCode.STORAGE_UNSAFE] == "storage_unsafe"
    assert table[PublicErrorCode.STORAGE_CORRUPT] == "storage_corrupt"


def test_session_start_storage_codes_carry_distinct_retryability(tmp_path: Path) -> None:
    """STORAGE_UNSAFE says retry once; STORAGE_CORRUPT says stop and escalate (#338)."""

    unsafe = _session_start_context_for_failure(
        tmp_path,
        "codex-storage-unsafe",
        _failure_result(PublicErrorCode.STORAGE_UNSAFE, retryable=True),
    )
    corrupt = _session_start_context_for_failure(
        tmp_path,
        "codex-storage-corrupt",
        _failure_result(PublicErrorCode.STORAGE_CORRUPT),
    )
    assert unsafe == hooks_module._STORAGE_UNSAFE_CONTEXT  # pyright: ignore[reportPrivateUsage]
    assert corrupt == hooks_module._STORAGE_CORRUPT_CONTEXT  # pyright: ignore[reportPrivateUsage]
    assert unsafe != corrupt
    assert "storage_unsafe" in unsafe and "Retry" in unsafe
    assert "storage_corrupt" in corrupt and "Do not retry" in corrupt
    for text in (unsafe, corrupt):
        assert text != hooks_module._UNAVAILABLE_CONTEXT  # pyright: ignore[reportPrivateUsage]
        assert "unavailable" not in text
        assert len(text) <= 2_000
    rows = [
        json.loads(line)
        for line in (tmp_path / "observation/hook-diagnostics.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    storage_rows = [row for row in rows if row["reason"].startswith("storage_")]
    assert [row["reason"] for row in storage_rows] == ["storage_unsafe", "storage_corrupt"]
    assert {row["event"] for row in storage_rows} == {"SessionStart"}


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


# Claude Code 2.1.251 passes an MCP tool's structured result to PostToolUse as one bare JSON
# string (captured live on 2026-09-04 with a probe MCP server that returned both a text block
# and structuredContent; issue #581). The Codex path shares the binder, so the same shape is
# admitted for both hosts.
CLAUDE_2_1_251_START_TOOL_RESPONSE = (
    '{"protocol_version":"0.1","schema_version":"1.0.0","ok":true,"outcome":"created",'
    '"task_id":"tsk_00000000-0000-4000-8000-000000000001",'
    '"session_id":"ses_00000000-0000-4000-8000-000000000002",'
    '"writer_id":"wri_00000000-0000-4000-8000-000000000003",'
    '"frontier":{"sequence":"1","head_digest":"sha256:' + "a" * 64 + '"}}'
)


def _diagnostics_text(state: Path) -> str:
    path = state / "observation" / "hook-diagnostics.jsonl"
    return path.read_text() if path.exists() else ""


def test_post_tool_use_bare_json_string_start_result_binds(tmp_path: Path) -> None:
    """The live 2.1.251 `tool_response` shape binds the session (#581)."""

    payload = {
        "session_id": "codex-string-1",
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_response": CLAUDE_2_1_251_START_TOOL_RESPONSE,
    }
    code = handle_post_tool_use(
        stdin_bytes=json.dumps(payload).encode("utf-8"), stdout=io.BytesIO(), _state=tmp_path
    )
    assert code == 0
    mapping = load_mapping("codex-string-1", _state=tmp_path)
    assert mapping is not None
    assert mapping.yoetz_task_id == "tsk_00000000-0000-4000-8000-000000000001"
    assert mapping.yoetz_session_id == "ses_00000000-0000-4000-8000-000000000002"
    assert mapping.yoetz_writer_id == "wri_00000000-0000-4000-8000-000000000003"
    assert mapping.last_frontier == "1:sha256:" + "a" * 64
    assert "start_bind" not in _diagnostics_text(tmp_path)


@pytest.mark.parametrize(
    ("tool_response", "reason"),
    [
        ("RESPONSE_CANARY", "start_bind_unparsed"),
        ({}, "start_bind_unparsed"),
        ({"structuredContent": {"ok": "true"}}, "start_bind_unparsed"),
        ('{"ok":null}', "start_bind_unparsed"),
        ({"content": "private output"}, "start_bind_unparsed"),
        ([{"type": "text", "text": "not json"}], "start_bind_unparsed"),
        (
            {"structuredContent": {"ok": True, "task_id": "tsk_not_an_id"}},
            "start_bind_invalid_ids",
        ),
        (
            {
                "structuredContent": {
                    "ok": True,
                    "task_id": "tsk_00000000-0000-4000-8000-000000000001",
                    "session_id": "ses_00000000-0000-4000-8000-000000000002",
                    "writer_id": "not-a-writer",
                }
            },
            "start_bind_invalid_ids",
        ),
    ],
)
def test_post_tool_use_unbound_start_result_records_typed_diagnostic(
    tmp_path: Path, tool_response: object, reason: str
) -> None:
    """A scoped start post-hook that binds nothing is no longer silent (#581)."""

    payload = {
        "session_id": "codex-unbound-1",
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_response": tool_response,
    }
    code = handle_post_tool_use(
        stdin_bytes=json.dumps(payload).encode("utf-8"), stdout=io.BytesIO(), _state=tmp_path
    )
    assert code == 0
    assert load_mapping("codex-unbound-1", _state=tmp_path) is None
    diagnostics = _diagnostics_text(tmp_path)
    assert diagnostics.count(f'"reason":"{reason}"') == 1
    assert '"event":"PostToolUse"' in diagnostics
    # Payload-free: the host response never lands in the diagnostics file.
    assert "RESPONSE_CANARY" not in diagnostics
    assert "private output" not in diagnostics


def test_post_tool_use_refused_start_records_no_bind_diagnostic(tmp_path: Path) -> None:
    """A start the service refused is nothing to bind, not a parsing failure."""

    for response in (
        {"structuredContent": {"ok": False}},
        '{"ok":false,"error":{"code":"SESSION_CONFLICT"}}',
    ):
        code = handle_post_tool_use(
            stdin_bytes=json.dumps(
                {
                    "session_id": "codex-refused-1",
                    "tool_name": "mcp__plugin_yoetz_yoetz__start",
                    "tool_response": response,
                }
            ).encode("utf-8"),
            stdout=io.BytesIO(),
            _state=tmp_path,
        )
        assert code == 0
    assert load_mapping("codex-refused-1", _state=tmp_path) is None
    assert "start_bind" not in _diagnostics_text(tmp_path)


def test_session_start_status_read_binds_the_workspace_locator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resume probe carries the same repository locator `start` does (#578)."""

    from yoetz.ports.control import WorkspaceLocator

    task_id, session_id, writer_id = _task_ids()
    store_mapping(
        mapping_from_start_ids(
            codex_session_id="codex-bound-1",
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier="0:genesis",
        ),
        _state=tmp_path,
    )
    client = _FakeClient()
    client.task_id, client.session_id, client.writer_id = task_id, session_id, writer_id
    captured: list[WorkspaceLocator | None] = []

    async def connect(
        _kind: ControlClientKind, *, workspace_locator: WorkspaceLocator | None = None
    ) -> _FakeClient:
        captured.append(workspace_locator)
        return client

    monkeypatch.setattr(hooks_module, "connect_service", connect)
    locator = str(tmp_path.resolve())

    stdout = io.BytesIO()
    code = handle_session_start(
        stdin_bytes=json.dumps({"session_id": "codex-bound-1", "source": "resume"}).encode(),
        stdout=stdout,
        _state=tmp_path,
        workspace=locator,
    )
    assert code == 0
    text = json.loads(stdout.getvalue().decode("utf-8"))["hookSpecificOutput"]["additionalContext"]
    assert session_id in text
    assert captured == [WorkspaceLocator(locator)]

    # Without an explicit --workspace the hook's own cwd is the locator, as for
    # every other repository-bound CLI call.
    monkeypatch.chdir(tmp_path)
    code = handle_session_start(
        stdin_bytes=json.dumps({"session_id": "codex-bound-1", "source": "compact"}).encode(),
        stdout=io.BytesIO(),
        _state=tmp_path,
    )
    assert code == 0
    assert captured == [WorkspaceLocator(locator), WorkspaceLocator(locator)]
    assert "mapping_stale" not in _diagnostics_text(tmp_path)


@pytest.mark.parametrize(
    ("reason_code", "kind"),
    [
        ("repository_identity_required", "workspace_unbound"),
        ("repository_identity_mismatch", "workspace_mismatch"),
    ],
)
def test_session_start_repository_fence_conflict_is_not_reported_stale(
    tmp_path: Path, reason_code: str, kind: str
) -> None:
    """A fence refusal is a live mapping the daemon could not bind, never `mapping_stale` (#578)."""

    text = _session_start_context_for_failure(
        tmp_path,
        f"codex-fence-{kind}",
        _failure_result(
            PublicErrorCode.SESSION_CONFLICT, safe_details={"reason_code": reason_code}
        ),
    )
    expected = (
        hooks_module._WORKSPACE_UNBOUND_CONTEXT  # pyright: ignore[reportPrivateUsage]
        if kind == "workspace_unbound"
        else hooks_module._WORKSPACE_MISMATCH_CONTEXT  # pyright: ignore[reportPrivateUsage]
    )
    assert text == expected
    assert "is stale" not in text
    assert "mode=attach" not in text
    assert f"status_{kind}" in text
    assert load_mapping(f"codex-fence-{kind}", _state=tmp_path) is not None
    diagnostics = _diagnostics_text(tmp_path)
    assert diagnostics.count(f'"reason":"status_{kind}"') == 1
    assert "mapping_stale" not in diagnostics


def test_session_start_superseded_session_names_the_replacement_binding(tmp_path: Path) -> None:
    """A genuinely replaced session still yields the stale advisory, now with the new ids (#578)."""

    new_task, new_session, new_writer = _task_ids()
    text = _session_start_context_for_failure(
        tmp_path,
        "codex-superseded",
        _failure_result(
            PublicErrorCode.SESSION_NOT_FOUND,
            safe_details={
                "reason_code": "session_superseded",
                "task_id": new_task,
                "session_id": new_session,
                "writer_id": new_writer,
            },
        ),
    )
    mapping = load_mapping("codex-superseded", _state=tmp_path)
    assert mapping is not None
    assert text.startswith("Yoetz task mapping for this session is stale")
    assert new_task in text
    assert f"session_id {new_session} and writer_id {new_writer}" in text
    assert f"mode=attach and session_id {mapping.yoetz_session_id}" in text
    assert "different external_ref" in text
    diagnostics = _diagnostics_text(tmp_path)
    assert diagnostics.count('"reason":"mapping_stale"') == 1


def test_session_start_superseded_with_invalid_replacement_ids_falls_back(tmp_path: Path) -> None:
    text = _session_start_context_for_failure(
        tmp_path,
        "codex-superseded-bad",
        _failure_result(
            PublicErrorCode.SESSION_NOT_FOUND,
            safe_details={
                "reason_code": "session_superseded",
                "task_id": "tsk_not_an_id",
                "session_id": "ses_not_an_id",
                "writer_id": "wri_not_an_id",
            },
        ),
    )
    mapping = load_mapping("codex-superseded-bad", _state=tmp_path)
    assert mapping is not None
    assert text == hooks_module._stale_mapping_context(mapping)  # pyright: ignore[reportPrivateUsage]
    assert "tsk_not_an_id" not in text
