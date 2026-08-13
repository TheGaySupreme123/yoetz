"""Codex lifecycle hook command handlers for the Yoetz CLI."""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Mapping
from importlib import resources
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast

from yoetz import __version__
from yoetz.adapters.integrations.codex_lifecycle import (
    LifecycleMapping,
    acquire_session_lock,
    clear_mapping,
    encode_frontier_token,
    load_mapping,
    mapping_from_start_ids,
    store_mapping,
    validate_codex_session_id,
)
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import OperationFailureModel, StatusRequest
from yoetz.service.client import connect_service

__all__ = [
    "INACTIVE_CONTEXT",
    "YOETZ_START_TOOL_NAMES",
    "handle_observe",
    "handle_post_tool_use",
    "handle_session_start",
    "handle_user_prompt_submit",
    "intake_cue_text",
    "read_hook_payload",
]


class _StatusClient(Protocol):
    async def status(self, request: StatusRequest, *, deadline_ms: int | None = None) -> object: ...

    async def close(self) -> None: ...


type ServiceConnector = Callable[[ControlClientKind], Awaitable[_StatusClient]]
type StatusOutcome = tuple[str, LifecycleMapping | None]
type AsyncRunner = Callable[[Callable[[], Awaitable[object]]], object]

_MAX_STDIN_BYTES: Final = 262_144
_MAX_CONTEXT_CHARS: Final = 2_000
_MAX_STDERR_CHARS: Final = 200
_STATUS_DEADLINE_MS: Final = 5_000
_INTAKE_CUE_BYTES: Final = 512

YOETZ_START_TOOL_NAMES: Final = frozenset({"start", "mcp__yoetz__start"})
INACTIVE_CONTEXT: Final = (
    "No Yoetz task is mapped to this session; call start before substantive material work."
)
_UNAVAILABLE_CONTEXT: Final = (
    "Yoetz service is unavailable for this mapped session; no live receipt can be promised."
)
_LOCKED_CONTEXT: Final = (
    "Yoetz vault is locked for this mapped session; no live receipt can be promised."
)


def _stderr_line(message: str) -> None:
    text = message.replace("\n", " ").replace("\r", " ")[:_MAX_STDERR_CHARS]
    try:
        sys.stderr.write(text + "\n")
        sys.stderr.flush()
    except OSError:
        pass


def _stdout_json(value: JsonValue, stream: BinaryIO | None = None) -> bool:
    out = sys.stdout.buffer if stream is None else stream
    try:
        out.write(canonical_encode(value) + b"\n")
        out.flush()
        return True
    except (BrokenPipeError, OSError, ValueError):
        return False


def _context_output(event_name: str, additional_context: str) -> dict[str, JsonValue]:
    text = additional_context.strip()
    if len(text) > _MAX_CONTEXT_CHARS:
        text = text[:_MAX_CONTEXT_CHARS]
    return {
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": text,
        }
    }


def read_hook_payload(raw: bytes | None = None) -> Mapping[str, JsonValue]:
    """Read a bounded Codex hook JSON object from stdin (or supplied bytes)."""

    data = sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1) if raw is None else raw
    if not data or len(data) > _MAX_STDIN_BYTES:
        raise ProtocolValueError("invalid_event_value_type")
    parsed = strict_json_parse(data)
    if not isinstance(parsed, Mapping):
        raise ProtocolValueError("unsupported_json_type")
    return cast(Mapping[str, JsonValue], parsed)


def intake_cue_text(*, resource_root: Path | None = None) -> str:
    """Return the first-512-byte intake cue from packaged agent instructions."""

    if resource_root is not None:
        data = (resource_root / "guidance" / "agent-instructions.md").read_bytes()
    else:
        node = resources.files("yoetz.resources")
        for part in ("guidance", "agent-instructions.md"):
            node = node.joinpath(part)
        data = node.read_bytes()
    cue = data[:_INTAKE_CUE_BYTES].decode("utf-8", errors="strict")
    # Prefer a clean paragraph boundary inside the first 512 UTF-8 bytes.
    cut = cue.rfind("\n\n")
    if cut > 64:
        cue = cue[:cut].rstrip()
    return cue.strip()


def handle_user_prompt_submit(
    *,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    resource_root: Path | None = None,
    _state: Path | None = None,
    workspace: str | None = None,
) -> int:
    """Inject the materiality/activation cue; route structural facts through observe."""

    try:
        raw = (
            stdin_bytes if stdin_bytes is not None else sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
        )
        _ = read_hook_payload(raw)
        cue = intake_cue_text(resource_root=resource_root)
        # Compatibility: keep trigger cue, then best-effort structural observe.
        from yoetz.cli.observe_hooks import handle_observe

        observe_out = __import__("io").BytesIO()
        handle_observe(
            event_name="UserPromptSubmit",
            stdin_bytes=raw,
            stdout=observe_out,
            workspace=workspace,
            _state=_state,
            skip_service=False,
        )
        _stdout_json(_context_output("UserPromptSubmit", cue), stdout)
        return 0
    except Exception:
        _stderr_line("hook_degraded: user-prompt-submit")
        try:
            cue = intake_cue_text(resource_root=resource_root)
            _stdout_json(_context_output("UserPromptSubmit", cue), stdout)
        except Exception:
            _stdout_json(_context_output("UserPromptSubmit", INACTIVE_CONTEXT), stdout)
        return 0


def _as_mapping(value: object) -> Mapping[str, JsonValue] | None:
    if isinstance(value, Mapping):
        return cast(Mapping[str, JsonValue], value)
    return None


def _extract_start_success(tool_response: object) -> Mapping[str, JsonValue] | None:
    mapping = _as_mapping(tool_response)
    if mapping is None:
        return None
    structured = mapping.get("structuredContent")
    if structured is None:
        structured = mapping.get("structured_content")
    candidate = _as_mapping(structured) if structured is not None else mapping
    if candidate is None:
        return None
    if candidate.get("ok") is not True:
        return None
    return candidate


def _frontier_from_start(result: Mapping[str, JsonValue]) -> str | None:
    frontier = result.get("frontier")
    frontier_map = _as_mapping(frontier)
    if frontier_map is None:
        return None
    sequence = frontier_map.get("sequence")
    digest = frontier_map.get("head_digest")
    if type(sequence) is not str or type(digest) is not str:
        return None
    try:
        return encode_frontier_token(sequence=sequence, head_digest=digest)
    except ProtocolValueError:
        return None


def handle_post_tool_use(
    *,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    _state: Path | None = None,
    workspace: str | None = None,
) -> int:
    """Correlate a successful Yoetz start MCP tool call; route structural observe."""

    try:
        raw = (
            stdin_bytes if stdin_bytes is not None else sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
        )
        payload = read_hook_payload(raw)
        tool_name = payload.get("tool_name")
        if type(tool_name) is str and tool_name in YOETZ_START_TOOL_NAMES:
            session_raw = payload.get("session_id")
            try:
                codex_session_id = validate_codex_session_id(session_raw)
            except ProtocolValueError:
                codex_session_id = None
            if codex_session_id is not None:
                success = _extract_start_success(payload.get("tool_response"))
                if success is not None:
                    frontier = _frontier_from_start(success)
                    mapping = mapping_from_start_ids(
                        codex_session_id=codex_session_id,
                        yoetz_task_id=cast(str, success.get("task_id")),
                        yoetz_session_id=cast(str, success.get("session_id")),
                        yoetz_writer_id=cast(str, success.get("writer_id")),
                        last_frontier=frontier,
                    )
                    store_mapping(mapping, _state=_state)
        from yoetz.cli.observe_hooks import handle_observe

        return handle_observe(
            event_name="PostToolUse",
            stdin_bytes=raw,
            stdout=stdout,
            workspace=workspace,
            _state=_state,
        )
    except Exception:
        _stderr_line("hook_degraded: post-tool-use")
        _stdout_json({}, stdout)
        return 0


def _active_context(mapping: LifecycleMapping, frontier: str | None) -> str:
    token = frontier if frontier is not None else mapping.last_frontier
    frontier_text = token if token is not None else "unknown"
    return (
        f"Yoetz task {mapping.yoetz_task_id} is mapped to this session at frontier "
        f"{frontier_text}. Call status before further material work."
    )


def _status_request(mapping: LifecycleMapping) -> StatusRequest:
    return StatusRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": new_id(IdKind.REQUEST),
            "actor": {
                "actor_id": "yoetz:codex-hooks",
                "actor_type": "harness",
            },
            "client": {
                "kind": "yoetz_cli",
                "version": __version__,
                "integration": "local_cli",
            },
            "session_id": mapping.yoetz_session_id,
            "writer_id": mapping.yoetz_writer_id,
            "view": "compact",
            "limit": "1",
            "at_frontier": None,
            "cursor": None,
        }
    )


async def _read_status(
    mapping: LifecycleMapping,
    *,
    connect: ServiceConnector = connect_service,
) -> StatusOutcome:
    """Return (context_kind, updated_mapping_or_none). kind is active|unavailable|locked."""

    client: _StatusClient | None = None
    try:
        connected = await connect(ControlClientKind.CLI)
        client = connected
        result = await connected.status(_status_request(mapping), deadline_ms=_STATUS_DEADLINE_MS)
        branch = getattr(result, "root", result)
        if isinstance(branch, OperationFailureModel):
            code = branch.error.code.value
            if code == "VAULT_LOCKED":
                return "locked", None
            return "unavailable", None
        head = getattr(branch, "head_frontier", None)
        task_id = getattr(branch, "task_id", None)
        session_id = getattr(branch, "session_id", None)
        writer_id = getattr(branch, "writer_id", None)
        if head is None or type(task_id) is not str or type(session_id) is not str:
            return "unavailable", None
        if type(writer_id) is not str:
            return "unavailable", None
        sequence = getattr(head, "sequence", None)
        digest = getattr(head, "head_digest", None)
        if type(sequence) is not str or type(digest) is not str:
            return "unavailable", None
        frontier = encode_frontier_token(sequence=sequence, head_digest=digest)
        updated = LifecycleMapping(
            mapping_version=mapping.mapping_version,
            codex_session_id=mapping.codex_session_id,
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier=frontier,
        )
        return "active", updated
    except ControlError as error:
        if error.reason == "vault_locked":
            return "locked", None
        return "unavailable", None
    except Exception:
        return "unavailable", None
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


def handle_session_start(
    *,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    _state: Path | None = None,
    connect: ServiceConnector = connect_service,
    run_async: AsyncRunner | None = None,
    workspace: str | None = None,
) -> int:
    """Re-ground after resume/compact; also route structural observe when consented."""

    import anyio

    runner: AsyncRunner = cast(AsyncRunner, anyio.run if run_async is None else run_async)
    try:
        raw = (
            stdin_bytes if stdin_bytes is not None else sys.stdin.buffer.read(_MAX_STDIN_BYTES + 1)
        )
        payload = read_hook_payload(raw)
        source = payload.get("source")
        if source == "startup":
            from yoetz.cli.observe_hooks import handle_observe

            return handle_observe(
                event_name="SessionStart",
                stdin_bytes=raw,
                stdout=stdout,
                workspace=workspace,
                _state=_state,
                connect=connect,
                run_async=run_async,
            )
        session_raw = payload.get("session_id")
        try:
            codex_session_id = validate_codex_session_id(session_raw)
        except ProtocolValueError:
            _stdout_json(_context_output("SessionStart", INACTIVE_CONTEXT), stdout)
            return 0
        if source == "clear":
            clear_mapping(codex_session_id, _state=_state)
            from yoetz.cli.observe_hooks import handle_observe

            handle_observe(
                event_name="SessionStart",
                stdin_bytes=raw,
                stdout=__import__("io").BytesIO(),
                workspace=workspace,
                _state=_state,
                connect=connect,
                run_async=run_async,
            )
            _stdout_json({}, stdout)
            return 0
        if source not in {"resume", "compact"}:
            from yoetz.cli.observe_hooks import handle_observe

            return handle_observe(
                event_name="SessionStart",
                stdin_bytes=raw,
                stdout=stdout,
                workspace=workspace,
                _state=_state,
                connect=connect,
                run_async=run_async,
            )
        with acquire_session_lock(codex_session_id, _state=_state) as owned:
            if not owned:
                # Another concurrent handler is already re-grounding this session.
                _stdout_json({}, stdout)
                return 0
            mapping = load_mapping(codex_session_id, _state=_state)
            if mapping is None:
                from yoetz.cli.observe_hooks import handle_observe

                observe_out = __import__("io").BytesIO()
                handle_observe(
                    event_name="SessionStart",
                    stdin_bytes=raw,
                    stdout=observe_out,
                    workspace=workspace,
                    _state=_state,
                    connect=connect,
                    run_async=run_async,
                )
                observed = observe_out.getvalue()
                if observed and observed not in {b"{}\n", b"{}\r\n"}:
                    if stdout is not None:
                        stdout.write(observed)
                        stdout.flush()
                    else:
                        sys.stdout.buffer.write(observed)
                        sys.stdout.buffer.flush()
                    return 0
                _stdout_json(_context_output("SessionStart", INACTIVE_CONTEXT), stdout)
                return 0

            async def _run() -> StatusOutcome:
                return await _read_status(mapping, connect=connect)

            kind, updated = cast(StatusOutcome, runner(_run))
            from yoetz.cli.observe_hooks import handle_observe

            handle_observe(
                event_name="SessionStart",
                stdin_bytes=raw,
                stdout=__import__("io").BytesIO(),
                workspace=workspace,
                _state=_state,
                connect=connect,
                run_async=run_async,
            )
            if kind == "active" and updated is not None:
                store_mapping(updated, _state=_state)
                _stdout_json(
                    _context_output(
                        "SessionStart",
                        _active_context(updated, updated.last_frontier),
                    ),
                    stdout,
                )
                return 0
            if kind == "locked":
                _stdout_json(_context_output("SessionStart", _LOCKED_CONTEXT), stdout)
                return 0
            _stdout_json(_context_output("SessionStart", _UNAVAILABLE_CONTEXT), stdout)
            return 0
    except Exception:
        _stderr_line("hook_degraded: session-start")
        _stdout_json(_context_output("SessionStart", INACTIVE_CONTEXT), stdout)
        return 0


def handle_observe(
    *,
    event_name: str,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    workspace: str | None = None,
    _state: Path | None = None,
) -> int:
    """Compatibility export for the unified observe ingress."""

    from yoetz.cli.observe_hooks import handle_observe as _handle

    return _handle(
        event_name=event_name,
        stdin_bytes=stdin_bytes,
        stdout=stdout,
        workspace=workspace,
        _state=_state,
    )
