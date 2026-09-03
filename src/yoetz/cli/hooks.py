"""Codex lifecycle hook command handlers for the Yoetz CLI."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Awaitable, Callable, Mapping
from importlib import resources
from pathlib import Path
from types import MappingProxyType
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
from yoetz.cli.hook_io import (
    context_output as _context_output,
)
from yoetz.cli.hook_io import (
    read_hook_payload,
)
from yoetz.cli.hook_io import (
    stderr_line as _stderr_line,
)
from yoetz.cli.hook_io import (
    stdout_json as _stdout_json,
)
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.canonical import JsonValue, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import OperationFailureModel, StatusRequest
from yoetz.service.client import connect_service

__all__ = [
    "INACTIVE_CONTEXT",
    "YOETZ_START_TOOL_NAMES",
    "bind_start_mapping_from_hook",
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
_STATUS_DEADLINE_MS: Final = 5_000
_INTAKE_CUE_BYTES: Final = 512

YOETZ_START_TOOL_NAMES: Final = frozenset(
    {"start", "mcp__yoetz__start", "mcp__plugin_yoetz_yoetz__start"}
)
INACTIVE_CONTEXT: Final = (
    "No Yoetz task is mapped to this session; call start before substantive material work."
)
_UNAVAILABLE_CONTEXT: Final = (
    "Yoetz service is unavailable for this mapped session; no live receipt can be promised."
)
_LOCKED_CONTEXT: Final = (
    "Yoetz vault is locked for this mapped session; no live receipt can be promised."
)


def _stale_mapping_context(mapping: LifecycleMapping) -> str:
    return (
        "Yoetz task mapping for this session is stale: the task was re-started under new "
        "session and writer ids. The service itself is healthy. Call start with mode=attach "
        f"and session_id {mapping.yoetz_session_id} (task {mapping.yoetz_task_id}) to continue "
        "the same task. Do not open a new task with a different external_ref."
    )


_RETRY_CONTEXT: Final = (
    "Yoetz is busy and could not read status on this attempt; the service is reachable. "
    "Call status before promising a receipt."
)
_PRIVACY_CONTEXT: Final = (
    "Yoetz cannot read this mapped session until repository privacy authority is "
    "granted; run 'yoetz --privacy'. No live receipt can be promised until then."
)
# The two storage outcomes carry opposite retry advice, so folding both into
# "unavailable" left an agent unable to tell a fault it may retry from data it
# must not keep writing to (#338). Both stay bounded and payload-free.
_STORAGE_UNSAFE_CONTEXT: Final = (
    "Yoetz storage faulted while reading this mapped session (storage_unsafe): the "
    "service is reachable and the stored data is not known to be damaged. Retry status "
    "once before promising a receipt; if it repeats, report it to the operator."
)
_STORAGE_CORRUPT_CONTEXT: Final = (
    "Yoetz stored data for this mapped session is invalid (storage_corrupt). Do not "
    "retry and do not promise a receipt; stop material work on this task and escalate "
    "to the operator, who can inspect it with 'yoetz status'."
)

# Hook-side classification of every public error a status read can surface. The
# daemon answering with SESSION_* means the stored mapping no longer names a live
# route/writer — the service is healthy, so reporting it "unavailable" was false
# and absorbing (issue #308). "retry" codes are transient reads that can succeed
# on the next attempt; only genuinely degraded states remain "unavailable".
# The two storage codes keep their own classes because they prescribe opposite
# next steps (#338): "storage_unsafe" is a fault that may be retried,
# "storage_corrupt" is invalid data that must not be.
_STATUS_ERROR_CLASSES: Final[Mapping[PublicErrorCode, str]] = MappingProxyType(
    {
        PublicErrorCode.SESSION_NOT_FOUND: "stale",
        PublicErrorCode.SESSION_CONFLICT: "stale",
        PublicErrorCode.VAULT_LOCKED: "locked",
        PublicErrorCode.OPERATION_PENDING: "retry",
        PublicErrorCode.BUNDLE_BUSY: "retry",
        PublicErrorCode.FRONTIER_CONFLICT: "retry",
        PublicErrorCode.PRIVACY_AUTHORITY_REQUIRED: "privacy",
        PublicErrorCode.INVALID_REQUEST: "unavailable",
        PublicErrorCode.PROTOCOL_VERSION_UNSUPPORTED: "unavailable",
        PublicErrorCode.IDEMPOTENCY_CONFLICT: "unavailable",
        PublicErrorCode.REQUEST_IDENTITY_CONFLICT: "unavailable",
        PublicErrorCode.EVENT_INVALID: "unavailable",
        PublicErrorCode.LIMIT_EXCEEDED: "unavailable",
        PublicErrorCode.STORAGE_UNSAFE: "storage_unsafe",
        PublicErrorCode.STORAGE_CORRUPT: "storage_corrupt",
        PublicErrorCode.MIGRATION_REQUIRED: "unavailable",
        PublicErrorCode.SERVICE_UNAVAILABLE: "unavailable",
        PublicErrorCode.PROVIDER_UNAVAILABLE: "unavailable",
        PublicErrorCode.PROVIDER_REFUSED: "unavailable",
        PublicErrorCode.PROVIDER_TIMEOUT: "unavailable",
        PublicErrorCode.SEMANTIC_RESULT_INVALID: "unavailable",
        PublicErrorCode.CANCELLED: "unavailable",
        PublicErrorCode.INTERNAL_ERROR: "unavailable",
    }
)
if set(_STATUS_ERROR_CLASSES) != set(PublicErrorCode):
    raise RuntimeError("status_error_classes_not_exhaustive")

# Transport-level failures, classified over the closed ControlError reason set.
# Reasons absent here (a future addition) fall back to "unavailable".
_CONTROL_ERROR_CLASSES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "vault_locked": "locked",
        "request_timeout": "retry",
        "service_draining": "retry",
        "service_generation_changed": "retry",
        "read_projection_failed": "retry",
        "response_projection_failed": "retry",
        "privacy_projection_unavailable": "retry",
        "privacy_projection_blocked": "privacy",
        "service_unavailable": "unavailable",
        "service_incompatible": "unavailable",
        "peer_untrusted": "unavailable",
        "endpoint_unsafe": "unavailable",
        "protocol_mismatch": "unavailable",
        "frame_invalid": "unavailable",
        "frame_too_large": "unavailable",
        "request_cancelled": "unavailable",
        "method_forbidden": "unavailable",
        "internal_error": "unavailable",
    }
)


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
    if mapping is not None:
        structured = mapping.get("structuredContent")
        if structured is None:
            structured = mapping.get("structured_content")
        candidate = _as_mapping(structured) if structured is not None else mapping
        if candidate is not None and candidate.get("ok") is True:
            return candidate
        content = mapping.get("content")
    else:
        content = tool_response

    # Claude may expose an MCP result as a content-block list instead of the
    # structuredContent object Codex supplies. Parse only one bounded text
    # block, then retain only the validated structural start identifiers below.
    if type(content) is not list:
        return None
    content_blocks = cast(list[object], content)
    if len(content_blocks) != 1:
        return None
    block = _as_mapping(content_blocks[0])
    if block is None or block.get("type") != "text":
        return None
    text = block.get("text")
    if type(text) is not str or len(text.encode("utf-8")) > _MAX_STDIN_BYTES:
        return None
    try:
        candidate = _as_mapping(strict_json_parse(text.encode("utf-8")))
    except Exception:
        return None
    if candidate is None or candidate.get("ok") is not True:
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


def bind_start_mapping_from_hook(
    payload: Mapping[str, JsonValue],
    *,
    _state: Path | None = None,
) -> bool:
    """Persist a validated mapping from one exact successful start hook result.

    The host response is inspected transiently. Only task/session/writer ids and
    the optional frontier token enter lifecycle storage.
    """

    tool_name = payload.get("tool_name")
    if type(tool_name) is not str or tool_name not in YOETZ_START_TOOL_NAMES:
        return False
    codex_session_id = validate_codex_session_id(payload.get("session_id"))
    success = _extract_start_success(payload.get("tool_response"))
    if success is None:
        return False
    mapping = mapping_from_start_ids(
        codex_session_id=codex_session_id,
        yoetz_task_id=cast(str, success.get("task_id")),
        yoetz_session_id=cast(str, success.get("session_id")),
        yoetz_writer_id=cast(str, success.get("writer_id")),
        last_frontier=_frontier_from_start(success),
    )
    store_mapping(mapping, _state=_state)
    return True


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
        with contextlib.suppress(ProtocolValueError):
            bind_start_mapping_from_hook(payload, _state=_state)
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


def _status_request(
    mapping: LifecycleMapping,
    *,
    actor_id: str = "yoetz:codex-hooks",
) -> StatusRequest:
    return StatusRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": new_id(IdKind.REQUEST),
            "actor": {
                "actor_id": actor_id,
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
    actor_id: str = "yoetz:codex-hooks",
) -> StatusOutcome:
    """Return (context_kind, updated_mapping_or_none).

    kind is active|stale|locked|retry|privacy|storage_unsafe|storage_corrupt|unavailable.
    """

    client: _StatusClient | None = None
    try:
        connected = await connect(ControlClientKind.CLI)
        client = connected
        result = await connected.status(
            _status_request(mapping, actor_id=actor_id), deadline_ms=_STATUS_DEADLINE_MS
        )
        branch = getattr(result, "root", result)
        if isinstance(branch, OperationFailureModel):
            return _STATUS_ERROR_CLASSES.get(branch.error.code, "unavailable"), None
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
        return _CONTROL_ERROR_CLASSES.get(error.reason, "unavailable"), None
    except Exception:
        return "unavailable", None
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass


# Issue #537: the SessionStart hook deliberately runs no applied-vs-serving drift probe.
# A hook process has no serving route of its own, so the only comparison available here is a
# `codex mcp get` subprocess (plus PATH version probes to find the binary), which costs a
# large fraction of the 2.2s end-to-end hook budget in `observe_hooks` and reintroduces the
# #209-#213 hook-latency loop. The MCP bridge (`yoetz mcp serve`) starts for the same Codex
# session, knows its own serving route from its argv, and emits the `registration_drift`
# diagnostic for free; that is the single emitter. See docs/runbooks/codex-integration.md.


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
            if kind == "stale":
                from yoetz.cli.hook_diagnostics import record_hook_diagnostic

                # The delegated observation path cannot reacquire the session lock
                # held here, so this branch owns the resume event's diagnostic.
                with contextlib.suppress(Exception):
                    record_hook_diagnostic("mapping_stale", "SessionStart", _state=_state)
                _stdout_json(
                    _context_output("SessionStart", _stale_mapping_context(mapping)), stdout
                )
                return 0
            if kind == "locked":
                _stdout_json(_context_output("SessionStart", _LOCKED_CONTEXT), stdout)
                return 0
            if kind == "retry":
                _stdout_json(_context_output("SessionStart", _RETRY_CONTEXT), stdout)
                return 0
            if kind == "privacy":
                _stdout_json(_context_output("SessionStart", _PRIVACY_CONTEXT), stdout)
                return 0
            if kind in {"storage_unsafe", "storage_corrupt"}:
                from yoetz.cli.hook_diagnostics import record_hook_diagnostic

                with contextlib.suppress(Exception):
                    record_hook_diagnostic(kind, "SessionStart", _state=_state)
                _stdout_json(
                    _context_output(
                        "SessionStart",
                        _STORAGE_UNSAFE_CONTEXT
                        if kind == "storage_unsafe"
                        else _STORAGE_CORRUPT_CONTEXT,
                    ),
                    stdout,
                )
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
