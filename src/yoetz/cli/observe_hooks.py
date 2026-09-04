"""Unified Codex hook observation ingress (structural envelopes only)."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, BinaryIO, Final, Literal, Protocol, cast

from yoetz.adapters.integrations.codex_lifecycle import (
    LifecycleMapping,
    acquire_session_lock,
    load_latest_mapping,
    load_mapping,
    mapping_from_start_ids,
    store_mapping,
    validate_codex_session_id,
)
from yoetz.adapters.integrations.hook_spool import HookSpool
from yoetz.adapters.integrations.observation_local import (
    HOOK_MAPPING_VERSION,
    YOETZ_OWNED_TOOL_NAMES,
    YOETZ_TOOL_NAMES,
    AdviceDelivery,
    FrontierMotionNotice,
    LocalObservationConsent,
    LocalObservationStore,
    ObservationOutboxRow,
    self_observation_deliverable,
)
from yoetz.cli import hook_io
from yoetz.cli.hook_diagnostics import record_hook_diagnostic, record_hook_timing
from yoetz.cli.hook_io import (
    claude_context_output as _claude_context_output,
)
from yoetz.cli.hook_io import (
    context_output as _context_output,
)
from yoetz.cli.hook_io import (
    cursor_context_output as _cursor_context_output,
)
from yoetz.cli.hook_io import (
    read_hook_payload,
)
from yoetz.cli.hook_io import (
    stderr_line as _stderr_line,
)
from yoetz.cli.workspace_binding import canonical_workspace_locator, resolve_workspace_locator
from yoetz.domain.observation import (
    ObservationContentChunk,
    ObservationContentKind,
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationSource,
    hook_source_commitment,
    observation_ingest_request_to_json,
    observation_ingest_result_from_json,
)
from yoetz.domain.values import (
    JsonObject,
    Timestamp,
    timestamp_from_datetime,
)
from yoetz.domain.values import (
    JsonValue as DomainJsonValue,
)
from yoetz.ports.integrations import YOETZ_WORKFLOW_TOOL_NAMES
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode

if TYPE_CHECKING:
    from yoetz.cli import hooks as hooks_cli
    from yoetz.ports.control import ControlClientKind

__all__ = [
    "ADVICE_SAFE_EVENTS",
    "STANDING_ADVICE_CADENCE_EVENTS",
    "SUPPORTED_HOOK_EVENTS",
    "handle_claude_observe",
    "handle_cursor_observe",
    "handle_observe",
    "handle_spool",
    "map_hook_payload_to_envelope",
]

SUPPORTED_HOOK_EVENTS: Final = frozenset(
    {
        "SessionStart",
        "SessionEnd",
        "Stop",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "PreCompact",
        "PostCompact",
        "SubagentStart",
        "SubagentStop",
    }
)
# Events that can deliver advice on a Codex-valid output contract. SessionEnd
# is excluded: the host discards its stdout, so a peek/commit there would
# consume advice the agent never sees (#222).
ADVICE_SAFE_EVENTS: Final = frozenset({"PostToolUse", "SessionStart", "Stop"})
# Standing machine conditions (connect_provider and kin) reach the agent at
# session boundaries only. PostToolUse is deliberately excluded: it is the
# per-tool-call channel that produced 29 byte-identical injections in one
# session (#241). Codex fires Stop per assistant turn, so the achievable bound
# is once per turn and only when a different advice text intervened. SessionEnd
# is teardown-only and cannot continue the agent.
STANDING_ADVICE_CADENCE_EVENTS: Final = frozenset({"SessionStart", "Stop"})
_MAX_ADVICE_CONTEXT: Final = 1_200
_MAX_CONTENT_CHUNK: Final = 256 * 1024
# Ingest rejections that are recoverable: keep the outbox entry pending for a
# later drain. Anything else is permanently invalid and gets quarantined so it
# is never silently dropped as if committed.
_HOOK_DRAIN_BUDGET_SECONDS: Final = 0.20
_HOOK_DRAIN_ROW_LIMIT: Final = 4
# Codex hard-clamps SessionEnd hooks to 3 seconds. The default drain budget
# plus ingest/encode overhead measured within ~0.5s of that ceiling on a
# realistic store, so SessionEnd drains under a tighter budget: an undrained
# row is retried on the next session's hooks, a SIGKILLed hook drains nothing.
_SESSION_END_DRAIN_BUDGET_SECONDS: Final = 0.15
# A run of consecutive service_unavailable rejections means the service is
# struggling now; yield the pass and let a later hook retry rather than
# spending the rest of the budget collecting identical failures.
_DRAIN_MAX_CONSECUTIVE_UNAVAILABLE: Final = 3
# Cold-connect preflight. A hook is always a fresh process, so this budget
# must clear a *cold* handshake, not a warm one: post-#210 a cold connect
# measures tens of milliseconds (it was ~1.0s when the handshake built the
# 69-schema catalog), and 1.0s leaves margin for daemon contention without
# letting a dead daemon consume the whole drain budget.
_HOOK_CONNECT_PREFLIGHT_SECONDS: Final = 1.0
# SessionStart is the primary auto-attach point, but the daemon often spawns on
# demand and may miss a session's opening moments; without a later re-attempt an
# unmapped session stayed unmapped for its whole life and its outbox retried as
# mapping_missing forever (#275). Low-frequency, once-per-turn events only --
# never the PreToolUse/PostToolUse storm -- under a budget that keeps even the
# Codex 3s SessionEnd clamp honest (attach 1.0 + connect preflight 1.0 + drain).
_AUTO_ATTACH_RETRY_EVENTS: Final = frozenset({"UserPromptSubmit", "Stop", "SessionEnd"})
_AUTO_ATTACH_RETRY_BUDGET_SECONDS: Final = 1.0
_AUTO_ATTACH_START_DEADLINE_MS: Final = 5_000
# End-to-end observability contract for one hook pass, process start included.
# Never an abort point: the drain and preflight budgets own enforcement.
# Derived from the enforced budgets nested inside one pass plus an allowance
# for the local stages (import, store, advice), so the parts can never again
# drift past the whole and turn hook_budget_exceeded into noise (#288). The
# allowance covers the measured local cost on a full 1 MiB store with margin.
_HOOK_LOCAL_STAGE_ALLOWANCE_SECONDS: Final = 1.0
_HOOK_TOTAL_BUDGET_SECONDS: Final = (
    _HOOK_CONNECT_PREFLIGHT_SECONDS
    + _HOOK_DRAIN_BUDGET_SECONDS
    + _HOOK_LOCAL_STAGE_ALLOWANCE_SECONDS
)
_TIMING_REPORT_EVENTS: Final = frozenset({"SessionStart", "Stop", "SessionEnd"})
# The stages that partition one pass end to end, in order. 'advice' is nested
# inside 'store' and every 'store_*' accumulator spans the whole pass, so
# neither belongs in a sum against the total.
_PASS_PARTITION_STAGES: Final = ("import", "resolve", "store", "drain", "deliver")
_ROUTINE_READ_TOOLS: Final = frozenset(
    {
        "glob",
        "grep",
        "list_files",
        "read",
        "read_file",
        "search",
        "view_file",
    }
)
_SHELL_TOOLS: Final = frozenset(
    {"bash", "command", "exec", "exec_command", "local_shell", "run_terminal_cmd", "shell"}
)
_READ_ONLY_COMMANDS: Final = frozenset({"head", "ls", "pwd", "rg", "tail", "wc"})
_STRUCTURAL_ALLOW: Final = frozenset(
    {
        "tool_name",
        "exit_status",
        "correlation_id",
        "result_status",
        "permission_decision",
        "subagent_id",
        "duration_ms",
        "success",
        "denied",
        "hook_name",
        "tool_call_id",
        "parent_tool_call_id",
        "permission_kind",
        "decision_reason_code",
        "event_ordinal",
        "attempt",
        "claim_kind",
        "action",
        "changed_paths_digest",
        "mapping_hint",
        "capability_profile_id",
        "codex_version",
        "cursor_version",
        "model_id",
        "model_effort",
    }
)
_TOKEN_CHARS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/+-"
)
_MAX_TOKEN_CHARS: Final = 128
_CURSOR_SESSION_PREFIX: Final = "cursor:"
_CURSOR_ALIAS_FILE: Final = "cursor-session-aliases.json"
_CURSOR_ALIAS_LOCK: Final = "cursor-session-aliases.lock"
_MAX_CURSOR_ALIASES: Final = 64
_CURSOR_UNTESTED_PROFILE_ID: Final = "untested"
_CURSOR_VERSION_TO_PROFILE: Final = {
    "3.17.8": "cursor-ide-3.17.8",
}


type AsyncRunner = Callable[[Callable[[], Awaitable[object]]], object]
type ServiceConnector = hooks_cli.ServiceConnector


def __getattr__(name: str) -> object:
    """Resolve the service-client seam on demand.

    ``connect_service`` stays a patchable module attribute (tests bind it to a
    forbidden connector) without ``yoetz.service.client`` — and through it
    ``protocol.schemas``/jsonschema — being imported by hooks that never open a
    connection (#242).
    """

    if name == "connect_service":
        from yoetz.service.client import connect_service

        return connect_service
    raise AttributeError(name)


def _connect_service() -> object:
    """Return the connector, honoring a module-attribute override."""

    override = globals().get("connect_service")
    if override is not None:
        return override
    from yoetz.service.client import connect_service

    return connect_service


class _HookDrainClient(Protocol):
    async def observation_ingest(
        self, body: DomainJsonValue, *, deadline_ms: int | None = None
    ) -> DomainJsonValue: ...

    async def close(self) -> None: ...


type HookDrainConnector = Callable[[ControlClientKind], Awaitable[_HookDrainClient]]


class _StartClient(Protocol):
    async def start(self, request: object, *, deadline_ms: int | None = None) -> object: ...

    async def close(self) -> None: ...


type HookStartConnector = Callable[[ControlClientKind], Awaitable[_StartClient]]


@dataclass(frozen=True, slots=True)
class AutoAttachOutcome:
    """Result of one consented auto-attach attempt.

    Exactly one of the two fields is set: a start-derived mapping, or the closed
    hook-diagnostic reason explaining why no mapping resulted (#459). A None
    mapping without a reason is not a legal outcome.
    """

    mapping: LifecycleMapping | None
    reason: str | None
    recovered: bool = False

    def __post_init__(self) -> None:
        if (self.mapping is None) == (self.reason is None):
            raise ValueError("auto_attach_outcome_invalid")


# Service-side `start` refusals, classified over the exhaustive public error
# table into the closed hook-diagnostic vocabulary. Retry-later classes share
# `service_unavailable` because the turn-boundary retry (#275) covers them; the
# rest name a cause that a retry will not clear.
_AUTO_ATTACH_ERROR_REASONS: Final[Mapping[PublicErrorCode, str]] = MappingProxyType(
    {
        PublicErrorCode.INVALID_REQUEST: "auto_attach_refused",
        PublicErrorCode.PROTOCOL_VERSION_UNSUPPORTED: "auto_attach_refused",
        PublicErrorCode.SESSION_NOT_FOUND: "auto_attach_refused",
        PublicErrorCode.EVENT_INVALID: "auto_attach_refused",
        PublicErrorCode.LIMIT_EXCEEDED: "auto_attach_refused",
        PublicErrorCode.PROVIDER_REFUSED: "auto_attach_refused",
        PublicErrorCode.SEMANTIC_RESULT_INVALID: "auto_attach_refused",
        PublicErrorCode.SESSION_CONFLICT: "auto_attach_conflict",
        PublicErrorCode.IDEMPOTENCY_CONFLICT: "auto_attach_conflict",
        PublicErrorCode.REQUEST_IDENTITY_CONFLICT: "auto_attach_conflict",
        PublicErrorCode.OPERATION_PENDING: "service_unavailable",
        PublicErrorCode.FRONTIER_CONFLICT: "service_unavailable",
        PublicErrorCode.BUNDLE_BUSY: "service_unavailable",
        PublicErrorCode.MIGRATION_REQUIRED: "service_unavailable",
        PublicErrorCode.SERVICE_UNAVAILABLE: "service_unavailable",
        PublicErrorCode.PROVIDER_UNAVAILABLE: "service_unavailable",
        PublicErrorCode.PROVIDER_TIMEOUT: "service_unavailable",
        PublicErrorCode.CANCELLED: "service_unavailable",
        PublicErrorCode.INTERNAL_ERROR: "service_unavailable",
        PublicErrorCode.STORAGE_UNSAFE: "storage_unsafe",
        PublicErrorCode.STORAGE_CORRUPT: "storage_corrupt",
        PublicErrorCode.VAULT_LOCKED: "vault_locked",
        PublicErrorCode.PRIVACY_AUTHORITY_REQUIRED: "privacy_authority_required",
    }
)
if set(_AUTO_ATTACH_ERROR_REASONS) != set(PublicErrorCode):
    raise RuntimeError("auto_attach_error_reasons_not_exhaustive")

# Transport-level failures over the closed ControlError reason set; reasons
# absent here (a future addition) fall back to `service_unavailable`.
_AUTO_ATTACH_CONTROL_REASONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "vault_locked": "vault_locked",
        "request_timeout": "timeout",
        "privacy_projection_blocked": "privacy_authority_required",
    }
)


def _now() -> Timestamp:
    current = datetime.now(UTC)
    stamp = current.replace(microsecond=(current.microsecond // 1000) * 1000)
    return timestamp_from_datetime(stamp)


def _token_or_none(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > _MAX_TOKEN_CHARS:
        return None
    if any(ch not in _TOKEN_CHARS for ch in value):
        return None
    if value[0] in "._:/+-":
        return None
    return value


def _int_or_none(value: object) -> int | None:
    if type(value) is bool or type(value) is not int:
        return None
    if not 0 <= value <= 9_007_199_254_740_991:
        return None
    return value


def _bool_or_none(value: object) -> bool | None:
    return value if type(value) is bool else None


def _cursor_alias_paths(_state: Path | None) -> tuple[Path, Path]:
    from yoetz.config.paths import state_dir

    root = state_dir() if _state is None else _state
    directory = root / "observation"
    return directory / _CURSOR_ALIAS_FILE, directory / _CURSOR_ALIAS_LOCK


def _load_cursor_aliases(path: Path) -> dict[str, str]:
    import json

    try:
        if path.is_symlink() or path.stat().st_size > 65_536:
            return {}
        loaded = json.loads(path.read_bytes())
    except OSError, ValueError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    aliases: dict[str, str] = {}
    for key, value in cast(dict[object, object], loaded).items():
        conversation = _token_or_none(key)
        session = _token_or_none(value)
        if conversation is not None and session is not None:
            aliases[conversation] = session
    return aliases


def _aliased_cursor_session(conversation: str, *, _state: Path | None) -> str | None:
    """Return the session identifier a sessionStart validated for this conversation."""

    path, _lock = _cursor_alias_paths(_state)
    return _load_cursor_aliases(path).get(conversation)


def _bind_cursor_session_alias(conversation: str, session: str, *, _state: Path | None) -> None:
    """Durably bind one Cursor conversation to one validated session identifier.

    Both identifiers come from the same host payload, so the pair itself is the
    validation. The bounded local map lets later events that carry only the
    conversation identifier keep resolving to the same Yoetz session instead of
    splitting one host conversation across identities (#417).
    """

    import json

    from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir

    try:
        import fcntl
    except ImportError:  # pragma: no cover - supported hook hosts are POSIX
        fcntl = None  # type: ignore[assignment]

    path, lock_path = _cursor_alias_paths(_state)
    try:
        ensure_owner_only_dir(path.parent)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        lock_descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(lock_descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            aliases = _load_cursor_aliases(path)
            aliases.pop(conversation, None)
            aliases[conversation] = session
            while len(aliases) > _MAX_CURSOR_ALIASES:
                aliases.pop(next(iter(aliases)))
            encoded = json.dumps(aliases, separators=(",", ":"), sort_keys=True).encode("utf-8")
            temporary = path.with_name(path.name + ".tmp")
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, path)
        finally:
            os.close(lock_descriptor)
    except OSError, PathSafetyError, ValueError:
        return


def _cursor_capability_profile_id(cursor_version: object) -> str:
    """Map an exact Cursor version to its reviewed profile, else stay untested.

    Hook payloads may report a version from any Cursor surface. Only the IDE
    profile owns the reviewed native hook set; the recognized CLI profile has
    no hook cell and therefore remains ``untested`` at hook ingress. Importing
    the adapter here would pull in the full plugin/rendering stack, so keep the
    fail-closed table local and never infer support for a neighboring surface
    or version.
    """

    version = _token_or_none(cursor_version)
    if version is None:
        return _CURSOR_UNTESTED_PROFILE_ID
    return _CURSOR_VERSION_TO_PROFILE.get(version, _CURSOR_UNTESTED_PROFILE_ID)


def _resolve_cursor_workspace(
    payload: Mapping[str, JsonValue], explicit_workspace: str | None
) -> str | None:
    """Resolve a Cursor workspace without retaining the host's root list.

    ``workspace_binding`` owns the validation and source precedence.  This
    wrapper returns one resolved path and no payload data.
    """

    resolved = resolve_workspace_locator(
        explicit=explicit_workspace,
        payload=payload,
        env=os.environ,
    )
    # The sibling resolver already performs bounded lexical normalization and
    # rejects symlinked components.  Do not resolve the returned path again:
    # that would re-open the exact symlink race this boundary closes.
    return resolved if type(resolved) is str and resolved else None


def _routine_read_action(payload: Mapping[str, JsonValue]) -> bool:
    """Recognize a deliberately narrow, side-effect-free tool invocation.

    The returned bit is structural only; command text remains visible-content input and is never
    copied into the observation envelope. Ambiguous shell syntax fails closed to ordinary
    materialization. This is a rate policy, not a general shell-effect analyzer.
    """

    tool = _token_or_none(payload.get("tool_name"))
    if tool is None:
        return False
    lowered = tool.lower()
    if lowered in _ROUTINE_READ_TOOLS:
        return True
    if lowered not in _SHELL_TOOLS:
        return False
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return False
    nested = cast(Mapping[str, JsonValue], tool_input)
    raw = nested.get("cmd") or nested.get("command")
    if type(raw) is not str or not raw or len(raw) > 16_384:
        return False
    # Multiple commands, redirection, substitution, and pipelines require a real shell parser.
    # They remain ordinary observations rather than being optimistically labelled read-only.
    if any(marker in raw for marker in ("\n", "\r", ";", "&", "|", ">", "<", "`", "$(")):
        return False
    try:
        argv = shlex.split(raw, posix=True)
    except ValueError:
        return False
    if not argv:
        return False
    command = argv[0]
    if "/" in command or "\\" in command:
        # Path-qualified names are not the closed basename set; a local `./ls`
        # or `/tmp/head` is an arbitrary executable, not a trusted read tool.
        return False
    if command == "rg" and any(arg == "--pre" or arg.startswith("--pre=") for arg in argv[1:]):
        # ripgrep's preprocessor is an arbitrary executable, not a read primitive.
        return False
    if command in _READ_ONLY_COMMANDS:
        return True
    if command == "git" and len(argv) >= 2:
        if any(
            arg == "--ext-diff" or arg == "--output" or arg.startswith("--output=")
            for arg in argv[1:]
        ):
            # `--output` writes a file; `--ext-diff` runs an external helper.
            return False
        return argv[1] in {"diff", "log", "rev-parse", "show", "status"}
    return False


def _extract_structural(payload: Mapping[str, JsonValue], event_name: str) -> JsonObject:
    fields: dict[str, JsonValue] = {"hook_name": event_name}
    tool_name = _token_or_none(payload.get("tool_name"))
    if tool_name is not None:
        fields["tool_name"] = tool_name
    for key in (
        "correlation_id",
        "parent_tool_call_id",
        "permission_decision",
        "permission_kind",
        "decision_reason_code",
        "result_status",
        "subagent_id",
        "claim_kind",
        "action",
        "changed_paths_digest",
        "mapping_hint",
        "capability_profile_id",
        "codex_version",
        "cursor_version",
        "model_id",
        "model_effort",
    ):
        token = _token_or_none(payload.get(key))
        if token is not None and key in _STRUCTURAL_ALLOW:
            fields[key] = token
    # Codex names the host tool-call id ``tool_use_id``. Normalize it to the
    # canonical structural key here, with the host spelling taking precedence
    # over legacy aliases exactly as it does in the pairing path. The wire
    # schema enumerates structural fields, so the identity must land under
    # ``tool_call_id`` or it is discarded at the boundary (#274).
    tool_call_id = _token_or_none(payload.get("tool_use_id")) or _token_or_none(
        payload.get("tool_call_id")
    )
    if tool_call_id is not None:
        fields["tool_call_id"] = tool_call_id
    # Nested tool_input / tool_response never contribute prose — only structural scalars.
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, Mapping):
        nested = cast(Mapping[str, JsonValue], tool_input)
        for key in ("tool_name", "permission_kind", "claim_kind", "action", "mapping_hint"):
            token = _token_or_none(nested.get(key))
            if token is not None and key not in fields:
                fields[key] = token
        digest = _token_or_none(nested.get("changed_paths_digest"))
        if digest is not None and "changed_paths_digest" not in fields:
            fields["changed_paths_digest"] = digest
    success = _bool_or_none(payload.get("success"))
    denied = _bool_or_none(payload.get("denied"))
    if (
        event_name in {"PreToolUse", "PostToolUse"}
        and _routine_read_action(payload)
        and success is not False
        and denied is not True
    ):
        # Service-owned classification overrides an untrusted host-supplied action token.
        # Explicit failures and denials stay ordinary observations even when the
        # tool name would otherwise be a routine read.
        fields["action"] = "routine_read"
    for key in ("exit_status", "duration_ms", "event_ordinal", "attempt"):
        number = _int_or_none(payload.get(key))
        if number is not None:
            fields[key] = number
    for key in ("success", "denied"):
        flag = _bool_or_none(payload.get(key))
        if flag is not None:
            fields[key] = flag
    # Permission outcome aliases commonly seen in host payloads.
    decision = _token_or_none(payload.get("decision"))
    if decision is not None and "permission_decision" not in fields:
        fields["permission_decision"] = decision
    return JsonObject(fields)


def _source_identity(
    event_name: str,
    payload: Mapping[str, JsonValue],
    structural: JsonObject,
    *,
    event_ordinal: int,
) -> str:
    host_ids: dict[str, JsonValue] = {"event_ordinal": event_ordinal}
    for key in (
        "tool_use_id",
        "tool_call_id",
        "correlation_id",
        "event_id",
        "id",
        "parent_tool_call_id",
        "subagent_id",
        "turn_id",
        "_yoetz_spool_id",
    ):
        token = _token_or_none(payload.get(key))
        if token is not None:
            host_ids[key] = token
    material = JsonObject(
        {
            "event_kind": event_name,
            "host_ids": JsonObject(host_ids),
            "session_id": _token_or_none(payload.get("session_id")) or "unknown",
            "structural": structural,
        }
    )
    digest = canonical_digest(material).removeprefix("sha256:")
    return f"hook:{digest[:48]}"


def _is_pre_event(event_name: str) -> bool:
    return event_name in {"PreToolUse", "PreCompact", "SubagentStart", "PermissionRequest"}


def _is_post_event(event_name: str) -> bool:
    return event_name in {"PostToolUse", "PostCompact", "SubagentStop"}


def _event_ordinal_from_payload(payload: Mapping[str, JsonValue]) -> int | None:
    raw = payload.get("event_ordinal")
    if type(raw) is int and not isinstance(raw, bool) and raw >= 1:
        return raw
    return None


def map_hook_payload_to_envelope(
    event_name: str,
    payload: Mapping[str, JsonValue],
    *,
    session_commitment: str,
    event_ordinal: int,
    key_material: bytes,
    source_generation: int = 1,
    gap_codes: tuple[str, ...] = (),
    source: ObservationSource = ObservationSource.CODEX_HOOK,
) -> ObservationEnvelope:
    """Map a bounded hook payload to a structural ObservationEnvelope."""

    if event_name not in SUPPORTED_HOOK_EVENTS:
        structural = JsonObject({"hook_name": "unsupported"})
        gaps = tuple(
            sorted({*gap_codes, ObservationGapCode.UNSUPPORTED_EVENT.value}, key=str.encode)
        )
        identity = _source_identity(event_name, payload, structural, event_ordinal=event_ordinal)
        return ObservationEnvelope(
            session_commitment=session_commitment,
            event_kind=_token_or_none(event_name) or "unsupported_event",
            source_identity=identity,
            source=source,
            cursor=ObservationCursor(
                source_generation=source_generation,
                byte_position=0,
                event_position=event_ordinal,
                last_source_commitment=f"hmac-sha256:{'0' * 64}",
                mapping_version=HOOK_MAPPING_VERSION,
            ),
            receipt_time=_now(),
            structural_payload=structural,
            content_object_refs=(),
            gap_codes=gaps,
        )
    structural = _extract_structural(payload, event_name)
    if "event_ordinal" not in structural:
        structural = JsonObject({**structural, "event_ordinal": event_ordinal})
    identity = _source_identity(event_name, payload, structural, event_ordinal=event_ordinal)
    commitment = hook_source_commitment(key_material, identity)
    return ObservationEnvelope(
        session_commitment=session_commitment,
        event_kind=event_name,
        source_identity=identity,
        source=source,
        cursor=ObservationCursor(
            source_generation=source_generation,
            byte_position=0,
            event_position=event_ordinal,
            last_source_commitment=commitment,
            mapping_version=HOOK_MAPPING_VERSION,
        ),
        receipt_time=_now(),
        structural_payload=structural,
        content_object_refs=(),
        gap_codes=gap_codes,
    )


def _visible_content_chunks(
    event_name: str,
    payload: Mapping[str, JsonValue],
    *,
    envelope: ObservationEnvelope,
    workspace_locator: str | None,
) -> tuple[tuple[ObservationContentChunk, ...], bool]:
    """Extract only explicitly visible task content and redact it before transport.

    Returns ``(chunks, truncated)``. Caps set ``truncated`` so callers attach
    ``truncated_payload`` without inventing success.
    """

    from yoetz.observability.privacy import redact_sensitive_content

    selected: list[tuple[ObservationContentKind, str, bytes]] = []
    if (
        event_name in {"PreToolUse", "PostToolUse"}
        and _token_or_none(payload.get("tool_name")) in YOETZ_OWNED_TOOL_NAMES
    ):
        # Yoetz's own tool arguments and results are already durable in the
        # ledger the call wrote to or read from. Capturing them back as tool
        # input/output evidence re-encrypted every ``status`` projection the
        # agent read and was the heaviest part of the self-observation loop
        # (#564). Nothing is omitted that the service does not hold.
        return (), False

    def add(kind: ObservationContentKind, label: str, value: JsonValue) -> None:
        if type(value) is str and value:
            selected.append((kind, label, value.encode("utf-8")))
        elif isinstance(value, Mapping) or type(value) in {tuple, list}:
            try:
                selected.append((kind, label, canonical_encode(value)))
            except ProtocolValueError, TypeError, ValueError:
                return

    if event_name == "UserPromptSubmit":
        add(
            ObservationContentKind.VISIBLE_USER_MESSAGE,
            "user",
            payload.get("prompt") or payload.get("message"),
        )
    elif event_name in {"Stop", "AgentMessage"}:
        add(
            ObservationContentKind.VISIBLE_ASSISTANT_MESSAGE,
            "assistant",
            payload.get("message") or payload.get("output") or payload.get("content"),
        )
    elif event_name in {"SubagentStart", "SubagentStop"}:
        add(
            ObservationContentKind.VISIBLE_SUBAGENT_MESSAGE,
            "subagent",
            payload.get("message") or payload.get("output") or payload.get("content"),
        )
    elif event_name == "PreToolUse":
        add(ObservationContentKind.TOOL_INPUT, "tool-input", payload.get("tool_input"))
    elif event_name == "PostToolUse":
        add(
            ObservationContentKind.TOOL_OUTPUT,
            "tool-output",
            payload.get("tool_response") or payload.get("tool_output") or payload.get("output"),
        )
    elif event_name not in SUPPORTED_HOOK_EVENTS:
        # Unknown host events are retained only when the host marks their
        # payload visible. Hidden/system/developer/reasoning fields are never read.
        if payload.get("visibility") in {"user", "assistant", "tool", "task"}:
            add(
                ObservationContentKind.UNSUPPORTED_VISIBLE_PAYLOAD,
                "unsupported",
                payload.get("visible_content") or payload.get("message"),
            )

    for key, kind, label in (
        ("diff", ObservationContentKind.WORKSPACE_DIFF, "diff"),
        ("patch", ObservationContentKind.WORKSPACE_DIFF, "patch"),
        ("file_content", ObservationContentKind.CHANGED_FILE, "changed-file"),
    ):
        add(kind, label, payload.get(key))
    if event_name == "SessionStart" and workspace_locator is not None:
        add(ObservationContentKind.WORKSPACE_LOCATOR, "workspace", workspace_locator)

    chunks: list[ObservationContentChunk] = []
    remaining = 680_000
    truncated = False
    for selected_index, (kind, label, raw) in enumerate(selected):
        redacted, detected = redact_sensitive_content(raw)
        if not redacted:
            continue
        if len(redacted) > remaining:
            truncated = True
        redacted = redacted[:remaining]
        if not redacted:
            truncated = True
            break
        full_parts = [
            redacted[offset : offset + _MAX_CONTENT_CHUNK]
            for offset in range(0, len(redacted), _MAX_CONTENT_CHUNK)
        ]
        if len(full_parts) > 16:
            truncated = True
        parts = full_parts[:16]
        hit_chunk_cap = False
        for index, part in enumerate(parts):
            chunks.append(
                ObservationContentChunk(
                    content_kind=kind,
                    correlation_identity=f"{envelope.source_identity}:{label}",
                    source_commitment=envelope.cursor.last_source_commitment,
                    media_type="text/plain",
                    part_index=index,
                    part_count=len(parts),
                    content=part,
                    redacted=detected,
                )
            )
            if len(chunks) >= 16:
                hit_chunk_cap = True
                if index + 1 < len(parts):
                    truncated = True
                break
        remaining -= len(redacted)
        if hit_chunk_cap:
            if selected_index + 1 < len(selected):
                truncated = True
            break
    return tuple(chunks), truncated


def _elapsed_ms(started: float, finished: float) -> int:
    return max(0, int((finished - started) * 1000))


def _hook_total_budget_seconds(event: str) -> float:
    """Return the end-to-end budget for one pass of *event*.

    Events that may legitimately retry auto-attach (and SessionStart, the
    primary attach point) carry that enforced budget on top of the base sum;
    the high-frequency PreToolUse/PostToolUse storm never attaches and keeps
    the tighter contract (#288).
    """

    if event == "SessionStart" or event in _AUTO_ATTACH_RETRY_EVENTS:
        return _HOOK_TOTAL_BUDGET_SECONDS + _AUTO_ATTACH_RETRY_BUDGET_SECONDS
    return _HOOK_TOTAL_BUDGET_SECONDS


def _record_pass_timing(
    event: str,
    *,
    entry_started: float,
    stages: Mapping[str, int],
    monotonic: Callable[[], float],
    _state: Path | None,
) -> None:
    """Record the end-to-end hook budget.

    Observability only: exceeding the budget never aborts a pass, because that
    would drop ingest. The drain and preflight budgets stay the enforcement
    points. Rows are emitted only over budget or at a session boundary, so the
    64 KiB diagnostics window keeps its failure-reason history.
    """

    with contextlib.suppress(BaseException):
        total_ms = _elapsed_ms(entry_started, monotonic())
        over = total_ms > int(_hook_total_budget_seconds(event) * 1000)
        if not over and event not in _TIMING_REPORT_EVENTS:
            return
        if over:
            record_hook_diagnostic("hook_budget_exceeded", event, _state=_state)
        # What the partition does not cover is reported as its own term rather
        # than left for a reader to derive by knowing which stages nest. A row
        # whose stages sum to half its total said nothing about the other half
        # (#310/#311); now it names it.
        attributed = sum(stages.get(name, 0) for name in _PASS_PARTITION_STAGES)
        record_hook_timing(
            event,
            ms=total_ms,
            stages={
                **stages,
                "total": total_ms,
                "unattributed": max(0, total_ms - attributed),
            },
            _state=_state,
        )


def _cached_recommendation_context(*, _state: Path | None) -> str:
    from yoetz.application.recommendations import cached_pending_recommendations

    pending = cached_pending_recommendations(root=_state, limit=1)
    if not pending:
        return ""
    item = pending[0]
    return (
        f"Yoetz recommends: {item.title}. {item.summary} Explain this to the user and ask "
        f"for approval; if approved run 'yoetz recommend accept {item.id}', "
        f"otherwise 'yoetz recommend decline {item.id}'."
    )[:_MAX_ADVICE_CONTEXT]


def _frontier_motion_context(notice: FrontierMotionNotice) -> str:
    return (
        "Yoetz: task frontier moved from "
        f"{notice.from_sequence} to {notice.to_sequence} when the Yoetz observation writer "
        f"appended {notice.observation_record_count} ledger record(s). "
        "Held publish frontiers remain valid across observation-only motion; "
        "run status before an exact-frontier check."
    )


async def _try_service_ingest(
    client: _HookDrainClient,
    codex_session_id: str,
    envelope: ObservationEnvelope,
    *,
    content_chunks: tuple[ObservationContentChunk, ...] = (),
    deadline_ms: int,
) -> ObservationIngestResult:
    """Attempt one typed ingest through an already-open preflight client."""

    from yoetz.ports.control import ControlError

    try:
        body = observation_ingest_request_to_json(
            ObservationIngestRequest(
                codex_session_id=codex_session_id,
                envelope=envelope,
                content_chunks=content_chunks,
            )
        )
        raw = await client.observation_ingest(body, deadline_ms=deadline_ms)
        try:
            return observation_ingest_result_from_json(raw)
        except ProtocolValueError, TypeError, ValueError:
            return ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.SERVICE_UNAVAILABLE.value,
                None,
            )
    except ControlError as error:
        reason = (
            ObservationGapCode.VAULT_LOCKED.value
            if error.reason == "vault_locked"
            else (
                ObservationGapCode.SERVICE_UNAVAILABLE.value
                if error.retryable
                else ObservationGapCode.LEDGER_REJECTED.value
            )
        )
        return ObservationIngestResult(ObservationIngestDisposition.REJECTED, reason, None)
    except Exception:
        return ObservationIngestResult(
            ObservationIngestDisposition.REJECTED,
            ObservationGapCode.SERVICE_UNAVAILABLE.value,
            None,
        )


async def _drain_outbox(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    codex_session_id: str,
    content_by_source_identity: Mapping[str, tuple[ObservationContentChunk, ...]] | None = None,
    connect: HookDrainConnector | None = None,
    event_name: str = "drain",
    _state: Path | None = None,
    budget_seconds: float = _HOOK_DRAIN_BUDGET_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Drain the workspace outbox under a nonblocking per-workspace lease.

    Codex runs async hooks concurrently; without the lease every concurrent
    hook re-ingests the identical backlog for zero extra delivery. Losing the
    lease means another live owner — a concurrent hook or the service sweeper —
    is already draining. That is the intended coordination, not a failure, so
    nothing is recorded (#351): one failure-shaped row per contending hook was
    exactly the diagnostic noise that buried genuine preflight/service faults.
    A crashed holder cannot wedge the lease; flock releases with its process.
    """

    with store.drain_lease(workspace_commitment) as owned:
        if not owned:
            return
        await _drain_outbox_leased(
            store,
            workspace_commitment=workspace_commitment,
            codex_session_id=codex_session_id,
            content_by_source_identity=content_by_source_identity,
            connect=connect,
            event_name=event_name,
            _state=_state,
            budget_seconds=budget_seconds,
            monotonic=monotonic,
        )


async def _drain_outbox_leased(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    codex_session_id: str,
    content_by_source_identity: Mapping[str, tuple[ObservationContentChunk, ...]] | None = None,
    connect: HookDrainConnector | None = None,
    event_name: str = "drain",
    _state: Path | None = None,
    budget_seconds: float = _HOOK_DRAIN_BUDGET_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Drain all mapped-session work fairly; ack only after service commit.

    The current session receives the first slot for low-latency hook feedback,
    then sessions are interleaved round-robin. A busy current session therefore
    cannot indefinitely filter or starve recovered work from another mapped
    session in the same workspace.
    """

    import asyncio

    from yoetz.application.observation_drain import (
        EXPECTED_OBSERVATION_BACKPRESSURE_REASONS,
        WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS,
        ObservationDrainAction,
        route_observation_ingest,
    )
    from yoetz.ports.control import ControlClientKind

    all_pending = store.list_pending_outbox_rows(workspace_commitment)
    if not all_pending:
        return

    connector = cast(HookDrainConnector, _connect_service()) if connect is None else connect
    client: _HookDrainClient
    try:
        client = await asyncio.wait_for(
            connector(ControlClientKind.CLI), timeout=_HOOK_CONNECT_PREFLIGHT_SECONDS
        )
    except Exception:
        record_hook_diagnostic("drain_preflight_failed", event_name, _state=_state)
        return
    # The budget clock starts after the connect: the preflight bounds connect
    # time on its own, and charging a slow-but-successful connect against the
    # drain budget could exhaust the whole budget before the first row (the
    # SessionEnd budget is smaller than the preflight by design).
    started = monotonic()

    grouped: dict[str, list[ObservationOutboxRow]] = {}
    for row in all_pending:
        grouped.setdefault(row.codex_session_id, []).append(row)
    session_order = sorted(grouped, key=str.encode)
    if codex_session_id in grouped:
        session_order.remove(codex_session_id)
        session_order.insert(0, codex_session_id)
    pending: list[ObservationOutboxRow] = []
    while grouped and len(pending) < _HOOK_DRAIN_ROW_LIMIT:
        for session_id in tuple(session_order):
            queue = grouped.get(session_id)
            if not queue:
                grouped.pop(session_id, None)
                continue
            pending.append(queue.pop(0))
            if not queue:
                grouped.pop(session_id, None)
            if len(pending) >= _HOOK_DRAIN_ROW_LIMIT:
                break
    # Retryable rejections split three ways by scope (the reason vocabulary is
    # RETRYABLE_OBSERVATION_REJECTIONS in application/observation_drain.py):
    # - mapping_missing is session-scoped and cannot heal mid-pass, so one
    #   rejection retires the rest of that session for this pass;
    # - vault_locked / observation_disabled / paused are workspace-global and
    #   cannot heal mid-pass, so they end the pass;
    # - service_unavailable is the catch-all for row-scoped and transient
    #   failures (bundle contention, one malformed envelope, a dropped reply).
    #   It must not poison other *sessions*, but it still retires its own
    #   session for the pass: the failed row stays pending at the head of its
    #   lane, and delivering a later row of the same session would advance the
    #   ingest cursor past it and destroy it as terminal cursor_stale (#272).
    #   A run of them across sessions means the service is genuinely
    #   struggling, so the pass yields after a few.
    # Re-attempting every row of a permanently-undeliverable backlog burned
    # the whole drain budget per hook forever — the recurrence tax of #211.
    skipped_sessions: set[str] = set()
    consecutive_unavailable = 0
    # The hook owns a bounded slice; the service sweeper owns bulk delivery.
    # Hitting the slice after moving backlog (acknowledged or quarantined rows)
    # is a capacity yield, not a failed drain, so budget expiry records a
    # diagnostic only when the pass made no progress at all (#351).
    progressed = 0
    try:
        for row in pending:
            if row.codex_session_id in skipped_sessions:
                continue
            remaining = budget_seconds - (monotonic() - started)
            if remaining <= 0:
                if progressed == 0:
                    record_hook_diagnostic("drain_budget_exhausted", event_name, _state=_state)
                break
            chunks = (
                ()
                if content_by_source_identity is None
                else content_by_source_identity.get(row.envelope.source_identity, ())
            )
            try:
                result = await asyncio.wait_for(
                    _try_service_ingest(
                        client,
                        row.codex_session_id,
                        row.envelope,
                        content_chunks=chunks,
                        deadline_ms=max(1, int(remaining * 1_000)),
                    ),
                    timeout=remaining,
                )
            except TimeoutError:
                if progressed == 0:
                    record_hook_diagnostic("drain_budget_exhausted", event_name, _state=_state)
                break
            decision = route_observation_ingest(result, row=row)
            # One batch per row, opened only after this row's RPC returned and
            # closed before the next one: the acknowledgement is never durable
            # ahead of the ingest it acknowledges, and the store lock never
            # spans a network wait (#242).
            expected_backpressure = decision.reason in EXPECTED_OBSERVATION_BACKPRESSURE_REASONS
            with store.batched(workspace_commitment):
                attempted = store.bump_outbox_row_attempt(
                    workspace_commitment, row, reason=decision.reason
                )
                if attempted is not None:
                    if decision.reason is not None and not expected_backpressure:
                        store.note_coverage_gap(workspace_commitment, decision.reason)
                    if chunks and decision.action is not ObservationDrainAction.ACKNOWLEDGE:
                        store.note_coverage_gap(
                            workspace_commitment,
                            ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                        )
                    if decision.action is ObservationDrainAction.QUARANTINE:
                        store.quarantine_outbox_row(
                            workspace_commitment,
                            attempted,
                            decision.reason or ObservationGapCode.SERVICE_UNAVAILABLE.value,
                        )
                        progressed += 1
                    elif decision.action is ObservationDrainAction.ACKNOWLEDGE:
                        store.acknowledge_outbox_row(workspace_commitment, attempted)
                        progressed += 1
            if attempted is None:
                continue
            if decision.reason is not None and not expected_backpressure:
                record_hook_diagnostic(decision.reason, event_name, _state=_state)
            if decision.action is ObservationDrainAction.RETRY:
                if decision.reason in WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS:
                    break
                # The lane's head row stays pending; skipping to a later row of
                # the same session would deliver it out of order (#272), so the
                # session retires for the pass whatever the retryable reason.
                skipped_sessions.add(row.codex_session_id)
                if decision.reason == ObservationGapCode.MAPPING_MISSING.value:
                    # The session ended while unmapped: nothing will ever map it, so
                    # its rows would otherwise retry forever. Terminalization takes
                    # the lifecycle lock so a concurrent attach that is still
                    # persisting its mapping wins this race (#275, #283 review).
                    moved = 0
                    with contextlib.suppress(Exception):
                        moved = store.quarantine_ended_unmapped_session(
                            workspace_commitment,
                            row.codex_session_id,
                            decision.reason,
                        )
                    if moved:
                        consecutive_unavailable = 0
                        continue
                if decision.reason is not None:
                    # Stamp the retired siblings with the shared cause so
                    # `observe status` never reports them as not_attempted.
                    with contextlib.suppress(Exception):
                        store.note_outbox_session_reason(
                            workspace_commitment,
                            row.codex_session_id,
                            decision.reason,
                        )
                if decision.reason == ObservationGapCode.SERVICE_UNAVAILABLE.value:
                    consecutive_unavailable += 1
                    if consecutive_unavailable >= _DRAIN_MAX_CONSECUTIVE_UNAVAILABLE:
                        break
                else:
                    consecutive_unavailable = 0
                continue
            consecutive_unavailable = 0
    finally:
        with contextlib.suppress(Exception):
            await client.close()


async def _try_auto_start(
    codex_session_id: str,
    *,
    _state: Path | None,
    harness_id: Literal["claude", "codex", "cursor"] = "codex",
    workspace_locator: str | None,
    recovery_mapping: LifecycleMapping | None = None,
    connect: HookStartConnector | None = None,
) -> AutoAttachOutcome:
    """Service `start` for consented SessionStart auto-attach, or a typed reason.

    The request carries the paired identity the start contract requires:
    ``workspace_ref`` is the canonical workspace locator the hook already bound
    consent to (the stable project identity), and ``external_ref`` is the
    host-session identity. The service persists only HMAC commitments of both;
    nothing here writes the raw locator. Without a locator there is no legal
    request, so the attempt stops before any service call (#459).

    Honesty: when this succeeds the mapping is start-derived. When it fails, callers keep an
    observation session binding only — later MCP/CLI ``start`` can merge.
    """

    if workspace_locator is None:
        return AutoAttachOutcome(None, "auto_attach_workspace_unbound")

    from pydantic import ValidationError

    from yoetz import __version__
    from yoetz.ports.control import ControlClientKind, ControlError, WorkspaceLocator
    from yoetz.protocol.ids import IdKind, new_id
    from yoetz.protocol.models import OperationFailureModel, StartRequest

    external_ref = f"{harness_id}-session:{codex_session_id.removeprefix(f'{harness_id}:')}"
    request_base = {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "actor": {
            "actor_id": f"yoetz:{harness_id}-observe",
            "actor_type": "harness",
        },
        "client": {
            "kind": "yoetz_cli",
            "version": __version__,
            "integration": "local_cli",
        },
        "task_title": f"{harness_id.title()} observation auto-attach",
        "requested_view": "compact",
    }
    try:
        request = StartRequest.model_validate(
            {
                **request_base,
                "request_id": new_id(IdKind.REQUEST),
                "mode": "create_or_attach",
                "external_ref": external_ref,
                "workspace_ref": workspace_locator,
            }
        )
        recovery_request = (
            None
            if recovery_mapping is None
            else StartRequest.model_validate(
                {
                    **request_base,
                    "request_id": new_id(IdKind.REQUEST),
                    "mode": "attach",
                    "session_id": recovery_mapping.yoetz_session_id,
                    "external_ref": external_ref,
                    "workspace_ref": workspace_locator,
                }
            )
        )
    except ValidationError, ValueError, TypeError:
        # An authoring defect in this function, never a runtime condition: it
        # must be visible, not collapsed into an absent mapping.
        return AutoAttachOutcome(None, "auto_attach_request_invalid")

    client: _StartClient | None = None
    attempt_started = time.monotonic()
    recovered_task_id: str | None = None

    def _remaining_deadline_ms() -> int:
        elapsed_ms = int((time.monotonic() - attempt_started) * 1_000)
        remaining = _AUTO_ATTACH_START_DEADLINE_MS - elapsed_ms
        if remaining <= 0:
            raise TimeoutError
        return remaining

    try:
        if connect is None:
            connector = cast(Callable[..., Awaitable[_StartClient]], _connect_service())
            client = await connector(
                ControlClientKind.CLI,
                workspace_locator=WorkspaceLocator(workspace_locator),
            )
        else:
            client = await connect(ControlClientKind.CLI)
        result = await client.start(request, deadline_ms=_remaining_deadline_ms())
        branch = getattr(result, "root", result)
        safe_details = (
            getattr(branch.error, "safe_details", None)
            if isinstance(branch, OperationFailureModel)
            else None
        )
        reason_code = (
            cast(Mapping[str, object], safe_details).get("reason_code")
            if isinstance(safe_details, Mapping)
            else None
        )
        if (
            recovery_request is not None
            and recovery_mapping is not None
            and isinstance(branch, OperationFailureModel)
            and branch.error.code is PublicErrorCode.SESSION_CONFLICT
            and reason_code == "workspace_task_exists"
        ):
            # The public error intentionally discloses no task selector. Recovery is
            # allowed only because the hook already holds a validated local selector
            # from an ended host session in this exact consented workspace.
            result = await client.start(
                recovery_request,
                deadline_ms=_remaining_deadline_ms(),
            )
            recovered_task_id = recovery_mapping.yoetz_task_id
    except ControlError as error:
        return AutoAttachOutcome(
            None, _AUTO_ATTACH_CONTROL_REASONS.get(error.reason, "service_unavailable")
        )
    except TimeoutError:
        return AutoAttachOutcome(None, "timeout")
    except Exception:
        return AutoAttachOutcome(None, "service_unavailable")
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()

    branch = getattr(result, "root", result)
    if isinstance(branch, OperationFailureModel):
        return AutoAttachOutcome(
            None, _AUTO_ATTACH_ERROR_REASONS.get(branch.error.code, "auto_attach_refused")
        )
    if getattr(branch, "ok", None) is not True:
        return AutoAttachOutcome(None, "auto_attach_result_invalid")
    task_id = getattr(branch, "task_id", None)
    session_id = getattr(branch, "session_id", None)
    writer_id = getattr(branch, "writer_id", None)
    if type(task_id) is not str or type(session_id) is not str or type(writer_id) is not str:
        return AutoAttachOutcome(None, "auto_attach_result_invalid")
    if recovered_task_id is not None and task_id != recovered_task_id:
        return AutoAttachOutcome(None, "auto_attach_result_invalid")
    frontier = getattr(branch, "frontier", None)
    last_frontier = None
    if frontier is not None:
        sequence = getattr(frontier, "sequence", None)
        digest = getattr(frontier, "head_digest", None)
        if type(sequence) is str and type(digest) is str:
            from yoetz.adapters.integrations.codex_lifecycle import encode_frontier_token

            try:
                last_frontier = encode_frontier_token(sequence=sequence, head_digest=digest)
            except Exception:
                return AutoAttachOutcome(None, "auto_attach_result_invalid")
    try:
        mapping = mapping_from_start_ids(
            codex_session_id=codex_session_id,
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier=last_frontier,
        )
    except Exception:
        return AutoAttachOutcome(None, "auto_attach_result_invalid")
    try:
        store_mapping(mapping, _state=_state)
    except Exception:
        return AutoAttachOutcome(None, "auto_attach_mapping_write_failed")
    if (
        recovered_task_id is not None
        and recovery_mapping is not None
        and recovery_mapping.codex_session_id != mapping.codex_session_id
    ):
        with contextlib.suppress(Exception):
            store_mapping(
                mapping_from_start_ids(
                    codex_session_id=recovery_mapping.codex_session_id,
                    yoetz_task_id=mapping.yoetz_task_id,
                    yoetz_session_id=mapping.yoetz_session_id,
                    yoetz_writer_id=mapping.yoetz_writer_id,
                    last_frontier=recovery_mapping.last_frontier,
                ),
                _state=_state,
            )
    return AutoAttachOutcome(mapping, None, recovered=recovered_task_id is not None)


def _host_session_matches(
    session_id: str, harness_id: Literal["claude", "codex", "cursor"]
) -> bool:
    """True when the stored host session id belongs to this hook family."""

    if harness_id == "claude":
        return session_id.startswith(_CLAUDE_SESSION_PREFIX)
    if harness_id == "cursor":
        return session_id.startswith(_CURSOR_SESSION_PREFIX)
    return not session_id.startswith((_CLAUDE_SESSION_PREFIX, _CURSOR_SESSION_PREFIX))


def _ended_workspace_recovery_mapping(
    store: LocalObservationStore,
    workspace_commitment: str,
    codex_session_id: str,
    *,
    harness_id: Literal["claude", "codex", "cursor"],
    _state: Path | None,
) -> LifecycleMapping | None:
    """Return the latest same-host selector when every other bound session has ended."""

    unambiguous = frozenset(store.unambiguous_codex_sessions_for_workspace(workspace_commitment))
    bound_sessions = store.codex_sessions_for_workspace(workspace_commitment)
    if any(
        session_id != codex_session_id
        and not store.codex_session_ended(workspace_commitment, session_id)
        for session_id in bound_sessions
    ):
        return None

    ended = tuple(
        session_id
        for session_id in bound_sessions
        if session_id != codex_session_id
        and store.codex_session_ended(workspace_commitment, session_id)
        and session_id in unambiguous
        and _host_session_matches(session_id, harness_id)
    )
    valid = tuple(
        mapping
        for session_id in ended
        if (mapping := load_mapping(session_id, _state=_state)) is not None
    )
    if len({mapping.yoetz_task_id for mapping in valid}) != 1:
        return None
    return load_latest_mapping(
        tuple(mapping.codex_session_id for mapping in valid),
        _state=_state,
    )


def _rewrite_one_ended_predecessor_mapping(
    store: LocalObservationStore,
    workspace_commitment: str,
    session_id: str,
    successor: LifecycleMapping,
    *,
    _state: Path | None,
) -> None:
    """Rewrite one ended predecessor if it still maps the recovered task."""

    if not store.codex_session_ended(workspace_commitment, session_id):
        return
    predecessor = load_mapping(session_id, _state=_state)
    if predecessor is None or predecessor.yoetz_task_id != successor.yoetz_task_id:
        return
    if (
        predecessor.yoetz_session_id == successor.yoetz_session_id
        and predecessor.yoetz_writer_id == successor.yoetz_writer_id
    ):
        return
    store_mapping(
        mapping_from_start_ids(
            codex_session_id=predecessor.codex_session_id,
            yoetz_task_id=successor.yoetz_task_id,
            yoetz_session_id=successor.yoetz_session_id,
            yoetz_writer_id=successor.yoetz_writer_id,
            last_frontier=predecessor.last_frontier,
        ),
        _state=_state,
    )


def _rewrite_ended_predecessor_mappings(
    store: LocalObservationStore,
    workspace_commitment: str,
    successor: LifecycleMapping,
    *,
    harness_id: Literal["claude", "codex", "cursor"],
    _state: Path | None,
    held_codex_session_id: str | None = None,
) -> None:
    """Point ended same-host mappings at the rotated successor route (#577)."""

    for session_id in store.codex_sessions_for_workspace(workspace_commitment):
        if session_id == successor.codex_session_id:
            continue
        if not store.codex_session_ended(workspace_commitment, session_id):
            continue
        if not _host_session_matches(session_id, harness_id):
            continue
        try:
            if session_id == held_codex_session_id:
                _rewrite_one_ended_predecessor_mapping(
                    store, workspace_commitment, session_id, successor, _state=_state
                )
                continue
            with acquire_session_lock(session_id, _state=_state) as owned:
                if not owned:
                    continue
                _rewrite_one_ended_predecessor_mapping(
                    store, workspace_commitment, session_id, successor, _state=_state
                )
        except Exception:
            continue


async def _try_workspace_auto_start(
    codex_session_id: str,
    *,
    store: LocalObservationStore,
    workspace_commitment: str,
    workspace_locator: str | None,
    harness_id: Literal["claude", "codex", "cursor"],
    _state: Path | None,
    connect: HookStartConnector | None,
) -> AutoAttachOutcome:
    """Auto-start, holding an ended predecessor stable through any recovery attach."""

    recovery = _ended_workspace_recovery_mapping(
        store,
        workspace_commitment,
        codex_session_id,
        harness_id=harness_id,
        _state=_state,
    )
    if recovery is None:
        return await _try_auto_start(
            codex_session_id,
            _state=_state,
            harness_id=harness_id,
            workspace_locator=workspace_locator,
            connect=connect,
        )

    with acquire_session_lock(recovery.codex_session_id, _state=_state) as predecessor_owned:
        if predecessor_owned:
            refreshed = _ended_workspace_recovery_mapping(
                store,
                workspace_commitment,
                codex_session_id,
                harness_id=harness_id,
                _state=_state,
            )
            if refreshed is not None and refreshed == recovery:
                outcome = await _try_auto_start(
                    codex_session_id,
                    _state=_state,
                    harness_id=harness_id,
                    workspace_locator=workspace_locator,
                    recovery_mapping=refreshed,
                    connect=connect,
                )
                if outcome.mapping is not None and outcome.recovered:
                    with contextlib.suppress(Exception):
                        _rewrite_ended_predecessor_mappings(
                            store,
                            workspace_commitment,
                            outcome.mapping,
                            harness_id=harness_id,
                            _state=_state,
                            held_codex_session_id=refreshed.codex_session_id,
                        )
                return outcome

    # A resumed predecessor or changed local state invalidates the capability.
    # Still run the ordinary request so the hook records the service's typed
    # conflict instead of inventing a local success or silently doing nothing.
    return await _try_auto_start(
        codex_session_id,
        _state=_state,
        harness_id=harness_id,
        workspace_locator=workspace_locator,
        connect=connect,
    )


def _record_auto_attach(
    outcome: AutoAttachOutcome,
    event_name: str,
    *,
    _state: Path | None,
) -> LifecycleMapping | None:
    """Surface a failed auto-attach as a payload-free diagnostic; return the mapping."""

    if outcome.mapping is not None:
        return outcome.mapping
    reason = outcome.reason if outcome.reason is not None else "service_unavailable"
    _stderr_line(f"hook_auto_attach_failed: {reason}")
    with contextlib.suppress(Exception):
        record_hook_diagnostic(reason, event_name, _state=_state)
    return None


def _note_dropped_event_gap(
    store: LocalObservationStore,
    payload: Mapping[str, JsonValue],
    workspace: str | None,
) -> None:
    """Record a coverage gap for an event dropped before the local pass ran.

    Mirrors the binding order of the main pass: the explicit workspace
    argument first, then the Codex-session→workspace map. A silently missing
    event is the one outcome this subsystem must not produce, so any drop
    must be visible to ``observe status`` and coverage wording.
    """

    commitment: str | None = None
    if workspace is not None:
        try:
            locator = canonical_workspace_locator(workspace)
            if locator is None:
                raise ValueError("workspace_locator_invalid")
            candidate = store.workspace_commitment(locator)
            consent = store.consent_for(candidate)
            if consent is not None and consent.active:
                commitment = candidate
        except Exception:
            commitment = None
    if commitment is None:
        codex_session_id = validate_codex_session_id(payload.get("session_id"))
        commitment = store.find_workspace_for_codex_session(codex_session_id)
    if commitment is None:
        return
    consent = store.consent_for(commitment)
    if consent is None or not consent.active:
        return
    store.note_coverage_gap(commitment, ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value)


def _consent_binding_diagnostic(consent: LocalObservationConsent | None) -> str:
    """Name why a resolved workspace admits no ingest: paused, or no active consent.

    Revocation wins over the retained pause flag, matching the `observe status`
    consent label: a revoked grant is unconsented, not paused.
    """

    if consent is not None and consent.revoked_at is None and consent.paused:
        return "paused"
    return "workspace_unconsented"


def handle_observe(
    *,
    event_name: str | None,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    workspace: str | None = None,
    _state: Path | None = None,
    connect: ServiceConnector | None = None,
    run_async: AsyncRunner | None = None,
    skip_service: bool = False,
    _entry_monotonic: float | None = None,
    _monotonic: Callable[[], float] = time.monotonic,
    _workspace_commitment: str | None = None,
    source: ObservationSource = ObservationSource.CODEX_HOOK,
    _output_event_name: str | None = None,
    _session_lock_owned: bool = False,
) -> int:
    """Bounded observation ingress for Codex lifecycle hooks. Always exits 0.

    ``skip_service`` keeps the hook fully local: capture, binding, and outbox
    enqueue still run, but no service connection is ever opened (auto-attach,
    mapped-session status, and outbox drains are all skipped). Advisory output
    that needs no service still runs — an unattached SessionStart binding emits
    the static "call start to attach a task" context either way (issue #280).

    ``_entry_monotonic`` is the console shim's pre-import sample; without it the
    recorded import stage reads zero rather than guessing.
    """

    entry_started = _monotonic() if _entry_monotonic is None else _entry_monotonic
    stages: dict[str, int] = {}
    try:
        store = LocalObservationStore(_state=_state)
        resolve_started = _monotonic()
        stages["import"] = _elapsed_ms(entry_started, resolve_started)
        raw_stdout_json = hook_io.stdout_json

        def _resolve_runner() -> AsyncRunner:
            """Resolve the async runner only on a branch that opens a connection."""

            if run_async is not None:
                return run_async
            import anyio

            return cast(AsyncRunner, anyio.run)

        def _stdout_json(value: JsonValue, stream: BinaryIO | None = None) -> bool:
            """Emit one JSON object; report whether the bytes actually left."""

            emitted = raw_stdout_json(value, stream)
            if not emitted:
                with contextlib.suppress(BaseException):
                    record_hook_diagnostic(
                        "stdout_write_failed", event_name or "unknown_event", _state=_state
                    )
            if stream is None and sys.stdout is sys.__stdout__:
                with contextlib.suppress(BaseException):
                    sys.stdout.flush()
                    sys.stdout.close()
            return emitted

        def _render_context(additional_context: str) -> dict[str, JsonValue]:
            """Render advice in the receiving host's own stdout contract.

            Cursor keeps its raw event name; Claude Code gets its documented
            non-error Stop feedback instead of Codex's `decision: block`.
            """

            if source is ObservationSource.CURSOR_HOOK:
                raw_cursor_event = _output_event_name
                if raw_cursor_event is None:
                    return {}
                return _cursor_context_output(raw_cursor_event, additional_context)
            if source is ObservationSource.CLAUDE_HOOK:
                # Claude requires hookSpecificOutput.hookEventName to name the
                # event that actually fired. PostToolUseFailure is normalized to
                # PostToolUse for Yoetz's internal observation/advice cadence, so
                # render with the preserved host event instead (#435).
                return _claude_context_output(
                    _output_event_name or resolved_event, additional_context
                )
            return _context_output(resolved_event, additional_context)

        payload = read_hook_payload(stdin_bytes)
        harness_id: Literal["claude", "codex", "cursor"] = (
            "claude"
            if source is ObservationSource.CLAUDE_HOOK
            else "cursor"
            if source is ObservationSource.CURSOR_HOOK
            else "codex"
        )
        raw_event = event_name or payload.get("hook_event_name")
        if type(raw_event) is not str or not raw_event:
            _stdout_json({}, stdout)
            return 0
        resolved_event = raw_event
        try:
            capture_enabled = store.runtime_enabled()
        except TimeoutError:
            # Store-lock contention says nothing about the gate itself.
            # Fall back to the missing-marker default (enabled) instead of
            # discarding the event; consent still gates every ingest below.
            _stderr_line("hook_observe_degraded: runtime_gate_contended")
            record_hook_diagnostic("runtime_gate_contended", resolved_event, _state=_state)
            capture_enabled = True
        except Exception:
            _stderr_line("hook_observe_degraded: runtime_gate_unsafe")
            record_hook_diagnostic("runtime_gate_unsafe", resolved_event, _state=_state)
            with contextlib.suppress(Exception):
                _note_dropped_event_gap(store, payload, workspace)
            _stdout_json({}, stdout)
            return 0
        if not capture_enabled:
            # The READY generation synchronized an explicit disabled config.
            # Stop before session binding, ordinals, capture, or outbox enqueue.
            if resolved_event == "SessionStart":
                additional = ""
                with contextlib.suppress(Exception):
                    additional = _cached_recommendation_context(_state=_state)
                _stdout_json(_render_context(additional) if additional else {}, stdout)
            else:
                _stdout_json({}, stdout)
            return 0
        session_raw = payload.get("session_id")
        try:
            codex_session_id = validate_codex_session_id(session_raw)
        except ProtocolValueError:
            _stderr_line("hook_observe_degraded: invalid_session")
            record_hook_diagnostic("invalid_session", resolved_event, _state=_state)
            _stdout_json({}, stdout)
            return 0

        workspace_commitment: str | None = None
        workspace_locator: str | None = None
        # Why a pass that ingests nothing ingested nothing. Every host ingress
        # shares this branch, so the reason is host-agnostic (#420/#435): an
        # explicit locator that cannot be canonicalized (`--workspace ""` from
        # an unset CLAUDE_PROJECT_DIR, a missing or symlinked path), a canonical
        # locator without consent, or a deliberately paused one. Without a
        # payload-free row here, `observe status` cannot tell a hook that fired
        # and was dropped from one that never fired.
        binding_diagnostic: str | None = None
        if _workspace_commitment is not None:
            workspace_commitment = _workspace_commitment
        elif workspace is not None:
            try:
                # Cursor already passed through the bounded host resolver. All
                # other hook sources use the same explicit-path canonicalizer
                # as consent/control so a Git subdirectory cannot acquire a
                # second commitment identity.
                workspace_locator = (
                    workspace
                    if source is ObservationSource.CURSOR_HOOK
                    else canonical_workspace_locator(workspace)
                )
            except Exception:
                workspace_locator = None
            if workspace_locator is None:
                binding_diagnostic = "workspace_unresolvable"
            else:
                try:
                    workspace_commitment = store.workspace_commitment(workspace_locator)
                    consent_probe = store.consent_for(workspace_commitment)
                    if consent_probe is None or not consent_probe.active:
                        binding_diagnostic = _consent_binding_diagnostic(consent_probe)
                        workspace_commitment = None
                        workspace_locator = None
                except Exception:
                    workspace_commitment = None
                    workspace_locator = None
        if workspace_commitment is None and workspace is None:
            # A hook without an explicit locator may retain the legacy bound-session/single-active
            # lane. An explicit locator that failed canonical consent never falls back to a
            # different commitment, including a legacy exact-subdirectory grant.
            workspace_commitment = store.find_workspace_for_codex_session(codex_session_id)
            workspace_locator = None
        consent = None if workspace_commitment is None else store.consent_for(workspace_commitment)
        if consent is None or not consent.active:
            # Consent missing/paused/revoked: no ingest, no spool; still exit 0,
            # but leave the typed, payload-free trace.
            if binding_diagnostic is None:
                binding_diagnostic = _consent_binding_diagnostic(consent)
            with contextlib.suppress(Exception):
                record_hook_diagnostic(binding_diagnostic, resolved_event, _state=_state)
            _stdout_json({}, stdout)
            return 0

        assert workspace_commitment is not None
        store_started = _monotonic()
        # Payload parse, runtime gate, workspace resolution and the consent
        # probe ran between 'import' and here, unwindowed. Three of those calls
        # take the store lock, so a contended pass spent real time in a region
        # no stage could name (#310/#311).
        stages["resolve"] = _elapsed_ms(resolve_started, store_started)
        # One flush for the whole local pass. The batch is closed before any
        # service RPC so an outbox acknowledgement can never become durable
        # ahead of the ingest it acknowledges, and it never spans a network
        # wait: it holds the interprocess store lock for its duration.
        with store.batched(workspace_commitment):
            if resolved_event == "SessionStart":
                # A recovery attach holds the predecessor's lifecycle lock. Gate
                # generation restart on that same lock so a resumed predecessor
                # cannot clear its ended marker between selection and attach.
                if _session_lock_owned:
                    session_commitment = store.bind_codex_session(
                        workspace_commitment, codex_session_id
                    )
                    source_generation = store.begin_session_generation(
                        workspace_commitment, session_commitment
                    )
                else:
                    with acquire_session_lock(codex_session_id, _state=_state) as generation_owned:
                        if not generation_owned:
                            _stdout_json({}, stdout)
                            return 0
                        session_commitment = store.bind_codex_session(
                            workspace_commitment, codex_session_id
                        )
                        source_generation = store.begin_session_generation(
                            workspace_commitment, session_commitment
                        )
            else:
                session_commitment = store.bind_codex_session(
                    workspace_commitment, codex_session_id
                )
                source_generation = store.current_session_generation(
                    workspace_commitment, session_commitment
                )
            gap_codes: list[str] = []

            # Prefer the host's canonical tool-use identity, while retaining
            # compatibility with earlier tool-call and correlation aliases.
            correlation = (
                _token_or_none(payload.get("tool_use_id"))
                or _token_or_none(payload.get("tool_call_id"))
                or _token_or_none(payload.get("correlation_id"))
            )
            if correlation is not None and _is_pre_event(resolved_event):
                store.note_open_pre(workspace_commitment, correlation, resolved_event)
            elif correlation is not None and _is_post_event(resolved_event):
                if not store.has_open_pre(workspace_commitment, correlation):
                    gap_codes.append(ObservationGapCode.UNPAIRED_EVENT.value)
                else:
                    store.consume_open_pre(workspace_commitment, correlation)

            if resolved_event not in SUPPORTED_HOOK_EVENTS:
                gap_codes.append(ObservationGapCode.UNSUPPORTED_EVENT.value)

            tool_name = _token_or_none(payload.get("tool_name"))
            skip_advice_loop = tool_name is not None and tool_name in YOETZ_TOOL_NAMES

            supplied_ordinal = _event_ordinal_from_payload(payload)
            event_ordinal = (
                supplied_ordinal
                if supplied_ordinal is not None
                else store.allocate_hook_ordinal(workspace_commitment, session_commitment)
            )

            envelope = map_hook_payload_to_envelope(
                resolved_event,
                payload,
                session_commitment=session_commitment,
                event_ordinal=event_ordinal,
                key_material=store.key_material(),
                source_generation=source_generation,
                gap_codes=tuple(sorted(set(gap_codes), key=str.encode)),
                source=source,
            )
            # Cursor publishes structural observation only. Its hook payloads
            # contain prompts, responses, transcript paths, file contents, and
            # MCP arguments/results that must never enter Yoetz content capture.
            if source in {ObservationSource.CLAUDE_HOOK, ObservationSource.CURSOR_HOOK}:
                content_chunks, content_truncated = (), False
            else:
                content_chunks, content_truncated = _visible_content_chunks(
                    resolved_event,
                    payload,
                    envelope=envelope,
                    workspace_locator=workspace_locator,
                )
            if content_truncated:
                envelope = replace(
                    envelope,
                    gap_codes=tuple(
                        sorted(
                            {*envelope.gap_codes, ObservationGapCode.TRUNCATED_PAYLOAD.value},
                            key=str.encode,
                        )
                    ),
                )
            content_map = {envelope.source_identity: content_chunks} if content_chunks else None

            # Local durable ingest first (never plaintext transcript spool).
            local_result = store.ingest(envelope)
            # A Yoetz-owned tool call is delivered only when it carries evidence
            # the service does not already hold from serving it (#564); the
            # envelope itself is always retained locally above.
            if local_result.disposition.value == "accepted" and self_observation_deliverable(
                resolved_event, envelope.structural_payload
            ):
                overflow = store.enqueue_outbox(workspace_commitment, codex_session_id, envelope)
                if overflow is not None:
                    if content_chunks:
                        store.note_coverage_gap(
                            workspace_commitment,
                            ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                        )
                    _stderr_line(f"hook_observe_degraded: {overflow}")
                    record_hook_diagnostic("outbox_overflow", resolved_event, _state=_state)

            # Persist session end so lifecycle can report STOPPED once every bound
            # session has ended.
            if resolved_event == "SessionEnd":
                with contextlib.suppress(Exception):
                    store.note_session_end(
                        workspace_commitment,
                        session_commitment,
                        generation=source_generation,
                    )

            # Cursor transcripts are outside its structural observation contract.
            # Only the Codex hook source may reconcile the secondary JSONL stream.
            if source is ObservationSource.CODEX_HOOK:
                with contextlib.suppress(Exception):
                    from yoetz.adapters.integrations.codex_session_stream import (
                        CodexSessionStreamLocator,
                        reconcile_session_stream,
                        resolve_codex_home,
                        should_trigger_stream_reconcile,
                    )

                    hook_path = payload.get("session_file") or payload.get("transcript_path")
                    hook_path_token = hook_path if type(hook_path) is str else None
                    session_source = payload.get("source")
                    if should_trigger_stream_reconcile(
                        resolved_event,
                        last_reconcile_mono=store.last_stream_reconcile_mono(workspace_commitment),
                        session_source=session_source if type(session_source) is str else None,
                    ):
                        locator = CodexSessionStreamLocator(resolve_codex_home())
                        reconcile_session_stream(
                            store,
                            workspace_commitment=workspace_commitment,
                            session_commitment=session_commitment,
                            codex_session_id=codex_session_id,
                            locator=locator,
                            hook_provided_path=hook_path_token,
                        )

            # Deterministic advice from retained envelopes (works with zero MCP publications).
            advice_started = _monotonic()
            with contextlib.suppress(Exception):
                store.refresh_advice(workspace_commitment)
            stages["advice"] = _elapsed_ms(advice_started, _monotonic())

        stages["store"] = _elapsed_ms(store_started, _monotonic())

        # SessionStart: auto-start/attach first, persist mapping, then drain outbox.
        # Every branch below that opens a service connection is gated on
        # skip_service so local-only callers (e.g. the setup readiness probe)
        # never create or attach real ledger tasks.
        additional = ""
        attach_advisory_only = False
        mapping: LifecycleMapping | None = load_mapping(codex_session_id, _state=_state)
        drain_started = _monotonic()
        if resolved_event == "SessionStart":
            session_source_value = payload.get("source")
            if session_source_value != "clear":
                # SessionStart-only status/attach helpers: they drag protocol.models
                # and service.client, which no other event needs (#242).
                from yoetz.cli.hooks import (
                    _LOCKED_CONTEXT,  # pyright: ignore[reportPrivateUsage]
                    _PRIVACY_CONTEXT,  # pyright: ignore[reportPrivateUsage]
                    _RETRY_CONTEXT,  # pyright: ignore[reportPrivateUsage]
                    _STORAGE_CORRUPT_CONTEXT,  # pyright: ignore[reportPrivateUsage]
                    _STORAGE_UNSAFE_CONTEXT,  # pyright: ignore[reportPrivateUsage]
                    _UNAVAILABLE_CONTEXT,  # pyright: ignore[reportPrivateUsage]
                    _active_context,  # pyright: ignore[reportPrivateUsage]
                    _read_status,  # pyright: ignore[reportPrivateUsage]
                    _stale_mapping_context,  # pyright: ignore[reportPrivateUsage]
                )

                with acquire_session_lock(codex_session_id, _state=_state) as owned:
                    if owned:
                        mapping = load_mapping(codex_session_id, _state=_state)
                        if mapping is None and not skip_advice_loop:
                            if not skip_service:

                                async def _attach() -> AutoAttachOutcome:
                                    return await _try_workspace_auto_start(
                                        codex_session_id,
                                        store=store,
                                        workspace_commitment=workspace_commitment,
                                        _state=_state,
                                        harness_id=harness_id,
                                        workspace_locator=workspace_locator,
                                        connect=cast(HookStartConnector | None, connect),
                                    )

                                mapping = _record_auto_attach(
                                    cast(AutoAttachOutcome, _resolve_runner()(_attach)),
                                    resolved_event,
                                    _state=_state,
                                )
                            if mapping is None:
                                # Static advisory for the unattached binding: it needs no
                                # service, so local-only mode still emits it (issue #280).
                                additional = (
                                    "Yoetz observation is consented for this workspace; "
                                    "no ledger task is mapped yet (observation-derived binding "
                                    "only). Call start to attach a task."
                                )
                                attach_advisory_only = True
                            else:
                                additional = _active_context(mapping, mapping.last_frontier)
                        elif mapping is not None and not skip_service:
                            active_mapping = mapping

                            async def _status() -> object:
                                connector = cast(
                                    "hooks_cli.ServiceConnector",
                                    connect if connect is not None else _connect_service(),
                                )
                                return await _read_status(
                                    active_mapping,
                                    connect=connector,
                                    actor_id=f"yoetz:{harness_id}-hooks",
                                )

                            kind, updated = cast(
                                tuple[str, LifecycleMapping | None], _resolve_runner()(_status)
                            )
                            if kind == "active" and updated is not None:
                                store_mapping(updated, _state=_state)
                                mapping = updated
                                additional = _active_context(updated, updated.last_frontier)
                            elif kind == "stale":
                                # The service answered: only the stored mapping is stale
                                # (re-started elsewhere). Repair flows through the agent's
                                # own start via handle_post_tool_use; meanwhile the static
                                # advisory must not starve pending advice (issue #308).
                                additional = _stale_mapping_context(mapping)
                                attach_advisory_only = True
                                with contextlib.suppress(Exception):
                                    record_hook_diagnostic(
                                        "mapping_stale", resolved_event, _state=_state
                                    )
                            elif kind == "locked":
                                additional = _LOCKED_CONTEXT
                            elif kind == "retry":
                                additional = _RETRY_CONTEXT
                            elif kind == "privacy":
                                additional = _PRIVACY_CONTEXT
                            elif kind in {"storage_unsafe", "storage_corrupt"}:
                                # Opposite retry advice, so never the shared
                                # "unavailable" text (#338); the same token
                                # lands in hook diagnostics for `observe status`.
                                additional = (
                                    _STORAGE_UNSAFE_CONTEXT
                                    if kind == "storage_unsafe"
                                    else _STORAGE_CORRUPT_CONTEXT
                                )
                                with contextlib.suppress(Exception):
                                    record_hook_diagnostic(kind, resolved_event, _state=_state)
                            else:
                                additional = _UNAVAILABLE_CONTEXT
                        if not skip_service:

                            async def _drain() -> None:
                                await _drain_outbox(
                                    store,
                                    workspace_commitment=workspace_commitment,
                                    codex_session_id=codex_session_id,
                                    content_by_source_identity=content_map,
                                    connect=cast(HookDrainConnector | None, connect),
                                    event_name=resolved_event,
                                    _state=_state,
                                    monotonic=_monotonic,
                                )

                            with contextlib.suppress(Exception):
                                _resolve_runner()(_drain)

        # Issue #537: no applied-vs-serving drift probe runs on this path. A hook process
        # has no serving route of its own, so the only comparison available here is a
        # `codex mcp get` subprocess plus the PATH version probes needed to find the
        # binary — routinely a large fraction of `_HOOK_TOTAL_BUDGET_SECONDS` and bounded
        # only by the adapter's own 10s command timeout, which is the #209-#213 hook
        # latency loop again. The MCP bridge starts for the same Codex session, knows its
        # serving route from its own argv, and emits `registration_drift` for free.

        if (
            not skip_service
            and not skip_advice_loop
            and mapping is None
            and resolved_event in _AUTO_ATTACH_RETRY_EVENTS
        ):
            # Re-attempt auto-attach for a session that started while the service was
            # unreachable, so one missed SessionStart no longer costs the session's whole
            # record (#275). Bounded, and placed before the drain below so a fresh mapping
            # delivers this session's backlog in the same pass.
            with acquire_session_lock(codex_session_id, _state=_state) as owned:
                if owned:
                    mapping = load_mapping(codex_session_id, _state=_state)
                    if mapping is None:

                        async def _attach_retry() -> AutoAttachOutcome:
                            import asyncio

                            attach = _try_workspace_auto_start(
                                codex_session_id,
                                store=store,
                                workspace_commitment=workspace_commitment,
                                _state=_state,
                                harness_id=harness_id,
                                workspace_locator=workspace_locator,
                                connect=cast(HookStartConnector | None, connect),
                            )
                            return await asyncio.wait_for(
                                attach,
                                timeout=_AUTO_ATTACH_RETRY_BUDGET_SECONDS,
                            )

                        try:
                            outcome = cast(AutoAttachOutcome, _resolve_runner()(_attach_retry))
                        except TimeoutError:
                            outcome = AutoAttachOutcome(None, "timeout")
                        except Exception:
                            outcome = AutoAttachOutcome(None, "service_unavailable")
                        mapping = _record_auto_attach(outcome, resolved_event, _state=_state)
                        if mapping is None:
                            # The retry marker names the path; the typed reason
                            # above names the cause.
                            record_hook_diagnostic(
                                "auto_attach_retry_failed", resolved_event, _state=_state
                            )

        if not skip_service and resolved_event != "SessionStart":
            # Every later mapped hook drains the complete session outbox, so the
            # current envelope plus any stream-recovered or previously-pending
            # entries all reconcile. Retryable rejections stay pending and
            # permanently-invalid ones are quarantined (never dropped) by the
            # shared drain routing. Unmapped events remain pending until mapped.
            async def _drain_all() -> None:
                await _drain_outbox(
                    store,
                    workspace_commitment=workspace_commitment,
                    codex_session_id=codex_session_id,
                    content_by_source_identity=content_map,
                    connect=cast(HookDrainConnector | None, connect),
                    event_name=resolved_event,
                    _state=_state,
                    budget_seconds=(
                        _SESSION_END_DRAIN_BUDGET_SECONDS
                        if resolved_event == "SessionEnd"
                        else _HOOK_DRAIN_BUDGET_SECONDS
                    ),
                    monotonic=_monotonic,
                )

            with contextlib.suppress(Exception):
                _resolve_runner()(_drain_all)

        if content_chunks and skip_service:
            # Content is intentionally ephemeral. Without a ready mapped
            # service there is no encrypted destination, so retain only the
            # structural envelope plus an explicit omission gap.
            store.note_coverage_gap(
                workspace_commitment,
                ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
            )

        deliver_started = _monotonic()
        stages["drain"] = _elapsed_ms(drain_started, deliver_started)

        # Advice selection and commit are serialized with the stdout write by a
        # dedicated delivery lease. The lease is independent of workspace state:
        # a blocked host pipe delays advice, never observation ingest or outbox work.
        # Commit remains after emit, so a failed write never suppresses a later delivery.
        pending_delivery: AdviceDelivery | None = None
        pending_frontier_notice: FrontierMotionNotice | None = None
        delivery_session_id: str | None = None
        # stop_hook_active is the host loop guard: a prior Stop already
        # continued this turn. Blocking again would loop; leave advice for a
        # later turn or SessionStart instead of consuming it here.
        stop_already_active = payload.get("stop_hook_active") is True
        # Task/receipt context always wins this shared channel outright, with one exception:
        # the static attach advisory carries no advice of its own, so pending advice joins it
        # (the delivery text is appended below) instead of being silently starved at the very
        # SessionStart that bootstraps an unmapped session (issues #241, #280).
        delivery_eligible = (
            (not additional or attach_advisory_only)
            and resolved_event in ADVICE_SAFE_EVENTS
            and not skip_advice_loop
            and not stop_already_active
            and (
                source is not ObservationSource.CURSOR_HOOK or _output_event_name == "sessionStart"
            )
        )
        delivery_gate = (
            store.advice_delivery_lease(workspace_commitment)
            if delivery_eligible
            else contextlib.nullcontext(False)
        )
        with delivery_gate as delivery_acquired:
            if delivery_eligible and delivery_acquired:
                delivery_session_id = None if mapping is None else mapping.yoetz_session_id
                if resolved_event == "PostToolUse":
                    pending_frontier_notice = store.peek_frontier_motion(
                        workspace_commitment, codex_session_id
                    )
                    if pending_frontier_notice is not None:
                        additional = _frontier_motion_context(pending_frontier_notice)
                delivery = store.peek_advice_for_delivery(
                    workspace_commitment,
                    yoetz_session_id=delivery_session_id,
                    allow_standing=resolved_event in STANDING_ADVICE_CADENCE_EVENTS,
                    session_commitment=session_commitment,
                )
                if delivery is not None:
                    additional = " ".join(part for part in (additional, delivery.text) if part)[
                        :_MAX_ADVICE_CONTEXT
                    ]
                    pending_delivery = delivery

            # Release recommendations are read from one bounded local cache only.
            # Existing task/receipt advice always wins this shared context channel.
            if (
                not additional
                and resolved_event == "SessionStart"
                and not skip_advice_loop
                and (
                    source is not ObservationSource.CURSOR_HOOK
                    or _output_event_name == "sessionStart"
                )
            ):
                with contextlib.suppress(Exception):
                    additional = _cached_recommendation_context(_state=_state)

            rendered_output = _render_context(additional) if additional else {}
            host_consumable = bool(rendered_output)
            if additional:
                emitted = _stdout_json(rendered_output, stdout)
            else:
                emitted = _stdout_json({}, stdout)
            if emitted and host_consumable and pending_delivery is not None:
                # Strictly after the write: delivered-but-unrecorded costs one
                # redelivery, recorded-but-undelivered would cost the advice.
                # Nothing past the emission may raise — the outer handler would
                # write a second JSON object onto a stream that already has one.
                with contextlib.suppress(BaseException):
                    store.commit_advice_delivery(
                        workspace_commitment,
                        pending_delivery.delivery_identity,
                        yoetz_session_id=delivery_session_id,
                        session_commitment=session_commitment,
                    )
            if emitted and host_consumable and pending_frontier_notice is not None:
                with contextlib.suppress(BaseException):
                    store.commit_frontier_motion_delivery(
                        workspace_commitment,
                        codex_session_id,
                        pending_frontier_notice.delivery_identity,
                        emitted_to_sequence=pending_frontier_notice.to_sequence,
                        emitted_task_id=pending_frontier_notice.task_id,
                        emitted_head_digest=pending_frontier_notice.head_digest,
                    )
        # Advice selection, the lease, the stdout write itself and both delivery
        # commits sit past the 'drain' window; a blocked host pipe or a
        # contended commit was previously invisible (#310/#311).
        stages["deliver"] = _elapsed_ms(deliver_started, _monotonic())
        # Attribute the whole pass's store work (#290), folded in last because
        # the store keeps saving after the 'store' stage window closes: the
        # drain saves once per delivered row and advice commits its own. A
        # snapshot taken at that window would have hidden exactly the rows the
        # 1300ms drains were made of. These are store-wide accumulations, not a
        # partition of 'store' — store_hydrate also covers the consent probe
        # that runs before the window opens.
        with contextlib.suppress(BaseException):
            for name, spent in store.stage_timings_ms.items():
                stages[f"store_{name}"] = max(0, int(spent))
        _record_pass_timing(
            resolved_event,
            entry_started=entry_started,
            stages=stages,
            monotonic=_monotonic,
            _state=_state,
        )
        return 0
    except BaseException:
        with contextlib.suppress(BaseException):
            _stderr_line("hook_observe_degraded: observe")
        with contextlib.suppress(BaseException):
            record_hook_diagnostic("observe", event_name or "observe", _state=_state)
        emitted = False
        with contextlib.suppress(BaseException):
            emitted = hook_io.stdout_json({}, stdout)
        if not emitted:
            with contextlib.suppress(BaseException):
                record_hook_diagnostic(
                    "stdout_write_failed", event_name or "observe", _state=_state
                )
        return 0


_CLAUDE_SESSION_PREFIX: Final = "claude:"
# Derived from the same exact tool-name set as the rendered hook matcher, so
# the renderer and this sanitizer allowlist cannot drift apart.
_CLAUDE_SCOPED_TOOL_RE: Final = re.compile(
    "^mcp__plugin_yoetz_yoetz__(?:" + "|".join(YOETZ_WORKFLOW_TOOL_NAMES) + ")$",
    re.ASCII,
)
_CLAUDE_UNTESTED_PROFILE_ID: Final = "untested"
_CLAUDE_VERSION_TO_PROFILE: Final = {
    "2.1.241": "claude-code-cli-local-project-2.1.241",
}


def _claude_capability_profile_id(claude_version: object) -> str:
    """Map an exact evidenced Claude version to its reviewed profile, else stay untested.

    The hook payload is the only version evidence this ingress has. A payload
    that names no version, or a neighboring version whose native contract was
    never proven, must not emit observations labeled with the evidenced
    ``2.1.241`` profile; the fail-closed table never infers a range.
    """

    token = _token_or_none(claude_version)
    if token is None:
        return _CLAUDE_UNTESTED_PROFILE_ID
    return _CLAUDE_VERSION_TO_PROFILE.get(token, _CLAUDE_UNTESTED_PROFILE_ID)


_CLAUDE_CHECK_TOOL_NAMES: Final = frozenset({"mcp__yoetz__check", "mcp__plugin_yoetz_yoetz__check"})


def _record_claude_permission_denied(
    payload: Mapping[str, JsonValue], *, _state: Path | None
) -> None:
    """Record that a host reviewer held a scoped semantic ``check`` (issue #467).

    ``source`` is Claude Code's closed origin token (``auto_mode`` | ``permission_rule`` |
    ``hook``). An absent source is attributed to auto mode, the only reviewer that produces a
    ``classifier_denied`` / ``no_verdict`` reason. Any other tool name is ignored: the rendered
    matcher is scoped to ``check`` and this ingress must not widen it.
    """

    if payload.get("tool_name") not in _CLAUDE_CHECK_TOOL_NAMES:
        return
    source = payload.get("source")
    reason = (
        "host_permission_rule_denied"
        if source in {"permission_rule", "hook"}
        else "host_auto_review_denied"
    )
    record_hook_diagnostic(reason, "PermissionDenied", _state=_state)


def handle_claude_observe(
    *,
    event_name: str | None,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    workspace: str | None = None,
    _state: Path | None = None,
    connect: ServiceConnector | None = None,
    run_async: AsyncRunner | None = None,
    skip_service: bool = False,
) -> int:
    """Normalize one Claude hook into structural-only Yoetz observation.

    The host payload can contain transcript/cwd paths, prompts, assistant text,
    complete tool inputs/responses, and raw errors.  None crosses this boundary.
    Only a closed lifecycle action, exact scoped Yoetz tool identity, bounded
    correlation token, and host-derived success bit are retained.
    """

    event_map = {
        "PostToolUse": "PostToolUse",
        "PostToolUseFailure": "PostToolUse",
        "SessionEnd": "SessionEnd",
        "SessionStart": "SessionStart",
        "Stop": "Stop",
    }
    start_actions = {
        "startup": "claude_session_startup",
        "resume": "claude_session_resume",
        "clear": "claude_session_clear",
        "compact": "claude_session_compact",
        "fork": "claude_session_fork",
    }
    try:
        payload = read_hook_payload(stdin_bytes)
        raw_event = event_name or payload.get("hook_event_name")
        if raw_event == "PermissionDenied":
            # Not an observation of work: the host refused the call before Yoetz saw it. Retain
            # only a closed reason token; tool input, reason prose, cwd, and ids are discarded.
            _record_claude_permission_denied(payload, _state=_state)
            hook_io.stdout_json({}, stdout)
            return 0
        if type(raw_event) is not str or raw_event not in event_map:
            hook_io.stdout_json({}, stdout)
            return 0
        session = _token_or_none(payload.get("session_id"))
        if session is None or len(session) > _MAX_TOKEN_CHARS - len(_CLAUDE_SESSION_PREFIX):
            hook_io.stdout_json({}, stdout)
            return 0
        structural: dict[str, JsonValue] = {
            "action": "claude_lifecycle",
            "capability_profile_id": _claude_capability_profile_id(
                payload.get("claude_code_version")
            ),
            "hook_event_name": event_map[raw_event],
            "session_id": f"{_CLAUDE_SESSION_PREFIX}{session}",
        }
        if raw_event == "Stop" and payload.get("stop_hook_active") is True:
            structural["stop_hook_active"] = True
        if raw_event == "SessionStart":
            source = payload.get("source")
            structural["action"] = (
                start_actions[source]
                if type(source) is str and source in start_actions
                else "claude_session"
            )
        if raw_event in {"PostToolUse", "PostToolUseFailure"}:
            tool_name = payload.get("tool_name")
            if type(tool_name) is not str or _CLAUDE_SCOPED_TOOL_RE.fullmatch(tool_name) is None:
                hook_io.stdout_json({}, stdout)
                return 0
            structural["action"] = (
                "claude_mcp_success" if raw_event == "PostToolUse" else "claude_mcp_failure"
            )
            structural["success"] = raw_event == "PostToolUse"
            structural["tool_name"] = tool_name
            correlation = _token_or_none(payload.get("tool_use_id"))
            if correlation is not None:
                structural["tool_use_id"] = correlation
            if raw_event == "PostToolUse" and tool_name == "mcp__plugin_yoetz_yoetz__start":
                # Claude's observation envelope stays structural-only, but the
                # successful start result is the sole authority that can bind
                # this host session to the cooperative Yoetz task. Inspect the
                # raw response transiently and persist only validated ids.
                from yoetz.cli.hooks import bind_start_mapping_from_hook

                with contextlib.suppress(Exception):
                    bind_start_mapping_from_hook(
                        cast(
                            Mapping[str, JsonValue],
                            {
                                "session_id": f"{_CLAUDE_SESSION_PREFIX}{session}",
                                "tool_name": tool_name,
                                "tool_response": payload.get("tool_response"),
                            },
                        ),
                        _state=_state,
                    )
        return handle_observe(
            event_name=event_map[raw_event],
            stdin_bytes=canonical_encode(structural),
            stdout=stdout,
            workspace=workspace,
            _state=_state,
            connect=connect,
            run_async=run_async,
            skip_service=skip_service,
            source=ObservationSource.CLAUDE_HOOK,
            _output_event_name=raw_event,
        )
    except BaseException:
        with contextlib.suppress(BaseException):
            hook_io.stdout_json({}, stdout)
        return 0


def handle_cursor_observe(
    *,
    event_name: str | None,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    workspace: str | None = None,
    _state: Path | None = None,
    connect: ServiceConnector | None = None,
    run_async: AsyncRunner | None = None,
    skip_service: bool = False,
) -> int:
    """Normalize one Cursor hook into structural-only Yoetz observation.

    Cursor supplies prompts, transcript paths, file paths/edits, MCP inputs/results,
    response text, email, and other user-controlled content in the same envelope.
    This boundary copies none of those values. Only bounded identifiers, exact host/
    model tokens, durations, booleans, and a one-way changed-path digest survive.
    """

    event_map = {
        "afterFileEdit": "PostToolUse",
        "afterMCPExecution": "PostToolUse",
        "sessionEnd": "SessionEnd",
        "sessionStart": "SessionStart",
        "stop": "Stop",
    }
    try:
        payload = read_hook_payload(stdin_bytes)
        raw_event = event_name or payload.get("hook_event_name")
        if type(raw_event) is not str or raw_event not in event_map:
            return 0 if hook_io.stdout_json({}, stdout) else 0
        session_token = _token_or_none(payload.get("session_id"))
        conversation_token = _token_or_none(payload.get("conversation_id"))
        # One host conversation must stay one Yoetz session (#417). sessionStart
        # validates the pair and persists the alias; later events that carry
        # only the conversation identifier resolve through it, and an event
        # whose pair contradicts the validated alias is an ambiguous transition
        # that is rejected rather than silently splitting the session.
        if session_token is not None and conversation_token is not None:
            aliased = _aliased_cursor_session(conversation_token, _state=_state)
            if raw_event == "sessionStart" or aliased is None:
                if conversation_token != session_token:
                    _bind_cursor_session_alias(conversation_token, session_token, _state=_state)
            elif aliased != session_token:
                with contextlib.suppress(Exception):
                    record_hook_diagnostic(
                        "cursor_session_ambiguous", event_map[raw_event], _state=_state
                    )
                return 0 if hook_io.stdout_json({}, stdout) else 0
            session = session_token
        elif session_token is None and conversation_token is not None:
            session = (
                _aliased_cursor_session(conversation_token, _state=_state) or conversation_token
            )
        else:
            session = session_token
        if session is None or len(session) > _MAX_TOKEN_CHARS - len(_CURSOR_SESSION_PREFIX):
            return 0 if hook_io.stdout_json({}, stdout) else 0
        resolved_workspace = _resolve_cursor_workspace(payload, workspace)
        if resolved_workspace is None:
            # No structural envelope is created until the workspace locator
            # is resolved.  In particular, Cursor's workspace_roots list is
            # never forwarded to the observation path or persisted.
            with contextlib.suppress(Exception):
                record_hook_diagnostic(
                    "workspace_unresolvable", event_map[raw_event], _state=_state
                )
            return 0 if hook_io.stdout_json({}, stdout) else 0
        cursor_version = _token_or_none(payload.get("cursor_version"))
        structural: dict[str, JsonValue] = {
            "action": (
                "cursor_file_edit"
                if raw_event == "afterFileEdit"
                else "cursor_mcp"
                if raw_event == "afterMCPExecution"
                else "cursor_lifecycle"
            ),
            "capability_profile_id": _cursor_capability_profile_id(cursor_version),
            "hook_event_name": event_map[raw_event],
            "session_id": f"{_CURSOR_SESSION_PREFIX}{session}",
        }
        for source_key, target_key in (
            ("cursor_version", "cursor_version"),
            ("generation_id", "correlation_id"),
            ("model_id", "model_id"),
            ("tool_name", "tool_name"),
        ):
            value = _token_or_none(payload.get(source_key))
            if value is not None:
                structural[target_key] = value
        duration = _int_or_none(payload.get("duration"))
        if duration is not None:
            structural["duration_ms"] = duration
        path_value = payload.get("file_path")
        if raw_event == "afterFileEdit" and type(path_value) is str and path_value:
            store = LocalObservationStore(_state=_state)
            structural["changed_paths_digest"] = hook_source_commitment(
                store.key_material(), f"cursor-path:{path_value}"
            )
            structural.setdefault("tool_name", "cursor_file_edit")
        parameters = payload.get("model_params")
        if type(parameters) is list:
            for item in parameters:
                if isinstance(item, Mapping) and item.get("id") == "effort":
                    effort = _token_or_none(item.get("value"))
                    if effort is not None:
                        structural["model_effort"] = effort
                    break
        return handle_observe(
            event_name=event_map[raw_event],
            stdin_bytes=canonical_encode(structural),
            stdout=stdout,
            workspace=resolved_workspace,
            _state=_state,
            connect=connect,
            run_async=run_async,
            skip_service=skip_service,
            source=ObservationSource.CURSOR_HOOK,
            _output_event_name=raw_event,
        )
    except BaseException:
        with contextlib.suppress(BaseException):
            hook_io.stdout_json({}, stdout)
        return 0


def handle_spool(
    *,
    event_name: str | None,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    workspace: str | None = None,
    _state: Path | None = None,
    _entry_monotonic: float | None = None,
    _monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Append one legacy synchronous ingress record and return immediately.

    No service preflight, local-state hydration, outbox drain, advice refresh,
    or observation-store write occurs on this host-critical path.
    """

    started = _monotonic() if _entry_monotonic is None else _entry_monotonic
    event = event_name or "observe"
    try:
        raw = stdin_bytes if stdin_bytes is not None else sys.stdin.buffer.read(_MAX_CONTENT_CHUNK)
        payload = read_hook_payload(raw)
        if workspace is not None and event_name in {
            "PreToolUse",
            "PermissionRequest",
            "PostToolUse",
        }:
            workspace_locator = canonical_workspace_locator(workspace)
            if workspace_locator is None:
                raise ValueError("workspace_locator_invalid")
            spool_payload = dict(payload)
            # Keep the safe classification, never the host command/input prose.
            if _routine_read_action(payload):
                spool_payload["action"] = "routine_read"
            HookSpool(_state=_state).append(
                workspace=workspace_locator,
                event_name=event_name,
                payload=spool_payload,
            )
    except Exception:
        record_hook_diagnostic("observe", event, _state=_state)
    finally:
        total = _elapsed_ms(started, _monotonic())
        # The p95 target is computed from retained host-visible timings; one
        # individual leg is a hard breach only after the 500ms ceiling.
        if total > 500:
            record_hook_diagnostic("hook_slo_breached", event, _state=_state)
        record_hook_timing(
            event,
            ms=total,
            stages={"total": total},
            path="sync_fallback_spool",
            _state=_state,
        )
        with contextlib.suppress(Exception):
            hook_io.stdout_json({}, stdout)
    return 0
