"""Unified Codex hook observation ingress (structural envelopes only)."""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final, Protocol, cast

from yoetz.adapters.integrations.codex_lifecycle import (
    LifecycleMapping,
    acquire_session_lock,
    load_mapping,
    mapping_from_start_ids,
    store_mapping,
    validate_codex_session_id,
)
from yoetz.adapters.integrations.observation_local import (
    HOOK_MAPPING_VERSION,
    YOETZ_TOOL_NAMES,
    AdviceDelivery,
    LocalObservationStore,
    ObservationOutboxRow,
)
from yoetz.cli import hook_io
from yoetz.cli.hook_diagnostics import record_hook_diagnostic, record_hook_timing
from yoetz.cli.hook_io import (
    context_output as _context_output,
)
from yoetz.cli.hook_io import (
    read_hook_payload,
)
from yoetz.cli.hook_io import (
    stderr_line as _stderr_line,
)
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
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError

if TYPE_CHECKING:
    from yoetz.cli import hooks as hooks_cli
    from yoetz.ports.control import ControlClientKind

__all__ = [
    "ADVICE_SAFE_EVENTS",
    "STANDING_ADVICE_CADENCE_EVENTS",
    "SUPPORTED_HOOK_EVENTS",
    "handle_observe",
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
ADVICE_SAFE_EVENTS: Final = frozenset({"PostToolUse", "SessionStart", "Stop", "SessionEnd"})
# Standing machine conditions (connect_provider and kin) reach the agent at
# session boundaries only. PostToolUse is deliberately excluded: it is the
# per-tool-call channel that produced 29 byte-identical injections in one
# session (#241). Codex fires Stop per assistant turn, so the achievable bound
# is once per turn and only when a different advice text intervened.
STANDING_ADVICE_CADENCE_EVENTS: Final = frozenset({"SessionStart", "Stop", "SessionEnd"})
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
# End-to-end observability contract for one hook pass, process start included.
# Never an abort point: the drain and preflight budgets own enforcement.
_HOOK_TOTAL_BUDGET_SECONDS: Final = 1.0
_TIMING_REPORT_EVENTS: Final = frozenset({"SessionStart", "Stop", "SessionEnd"})
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
    }
)
_TOKEN_CHARS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/+-"
)


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


def _now() -> Timestamp:
    current = datetime.now(UTC)
    stamp = current.replace(microsecond=(current.microsecond // 1000) * 1000)
    return timestamp_from_datetime(stamp)


def _token_or_none(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > 128:
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


def _extract_structural(payload: Mapping[str, JsonValue], event_name: str) -> JsonObject:
    fields: dict[str, JsonValue] = {"hook_name": event_name}
    tool_name = _token_or_none(payload.get("tool_name"))
    if tool_name is not None:
        fields["tool_name"] = tool_name
    for key in (
        "correlation_id",
        "tool_call_id",
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
    ):
        token = _token_or_none(payload.get(key))
        if token is not None and key in _STRUCTURAL_ALLOW:
            fields[key] = token
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
        "tool_call_id",
        "correlation_id",
        "event_id",
        "id",
        "parent_tool_call_id",
        "subagent_id",
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
            source=ObservationSource.CODEX_HOOK,
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
        source=ObservationSource.CODEX_HOOK,
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
        over = total_ms > int(_HOOK_TOTAL_BUDGET_SECONDS * 1000)
        if not over and event not in _TIMING_REPORT_EVENTS:
            return
        if over:
            record_hook_diagnostic("hook_budget_exceeded", event, _state=_state)
        record_hook_timing(event, ms=total_ms, stages={**stages, "total": total_ms}, _state=_state)


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
            else ObservationGapCode.SERVICE_UNAVAILABLE.value
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
    lease means another live hook process is already draining — not a failure,
    so nothing is recorded.
    """

    with store.drain_lease(workspace_commitment) as owned:
        if not owned:
            record_hook_diagnostic("drain_lease_contended", event_name, _state=_state)
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
    #   failures (bundle contention, one malformed envelope, a dropped reply),
    #   so it must NOT poison other rows — but a run of them in a row means
    #   the service is genuinely struggling, so the pass yields after a few.
    # Re-attempting every row of a permanently-undeliverable backlog burned
    # the whole drain budget per hook forever — the recurrence tax of #211.
    session_scoped_stop = ObservationGapCode.MAPPING_MISSING.value
    global_stop = frozenset(
        {
            ObservationGapCode.VAULT_LOCKED.value,
            "observation_disabled",
            "paused",
        }
    )
    skipped_sessions: set[str] = set()
    consecutive_unavailable = 0
    try:
        for row in pending:
            if row.codex_session_id in skipped_sessions:
                continue
            remaining = budget_seconds - (monotonic() - started)
            if remaining <= 0:
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
                record_hook_diagnostic("drain_budget_exhausted", event_name, _state=_state)
                break
            decision = route_observation_ingest(result)
            # One batch per row, opened only after this row's RPC returned and
            # closed before the next one: the acknowledgement is never durable
            # ahead of the ingest it acknowledges, and the store lock never
            # spans a network wait (#242).
            with store.batched(workspace_commitment):
                attempted = store.bump_outbox_row_attempt(
                    workspace_commitment, row, reason=decision.reason
                )
                if attempted is not None:
                    if decision.reason is not None:
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
                    elif decision.action is ObservationDrainAction.ACKNOWLEDGE:
                        store.acknowledge_outbox_row(workspace_commitment, attempted)
            if attempted is None:
                continue
            if decision.reason is not None:
                record_hook_diagnostic(decision.reason, event_name, _state=_state)
            if decision.action is ObservationDrainAction.RETRY:
                if decision.reason == ObservationGapCode.SERVICE_UNAVAILABLE.value:
                    consecutive_unavailable += 1
                    if consecutive_unavailable >= _DRAIN_MAX_CONSECUTIVE_UNAVAILABLE:
                        break
                    continue
                consecutive_unavailable = 0
                if decision.reason == session_scoped_stop:
                    skipped_sessions.add(row.codex_session_id)
                    # Stamp the retired siblings with the shared cause so
                    # `observe status` never reports them as not_attempted.
                    with contextlib.suppress(Exception):
                        store.note_outbox_session_reason(
                            workspace_commitment,
                            row.codex_session_id,
                            decision.reason,
                        )
                elif decision.reason in global_stop:
                    break
                continue
            consecutive_unavailable = 0
    finally:
        with contextlib.suppress(Exception):
            await client.close()


async def _try_auto_start(
    codex_session_id: str,
    *,
    _state: Path | None,
) -> LifecycleMapping | None:
    """Best-effort service start for consented SessionStart auto-attach.

    Honesty: when this succeeds the mapping is start-derived. When it fails, callers keep an
    observation session binding only — later MCP/CLI ``start`` can merge.
    """

    from yoetz import __version__
    from yoetz.ports.control import ControlClientKind
    from yoetz.protocol.ids import IdKind, new_id
    from yoetz.protocol.models import StartRequest

    connector = cast(Callable[[ControlClientKind], Awaitable[object]], _connect_service())
    client = None
    try:
        client = cast("_StartClient", await connector(ControlClientKind.CLI))
        request = StartRequest.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": new_id(IdKind.REQUEST),
                "actor": {"actor_id": "yoetz:codex-observe", "actor_type": "harness"},
                "client": {
                    "kind": "yoetz_cli",
                    "version": __version__,
                    "integration": "local_cli",
                },
                "mode": "create_or_attach",
                "task_title": "Codex observation auto-attach",
                "requested_view": "compact",
                "session_id": None,
                "external_ref": f"codex-session:{codex_session_id}",
                "workspace_ref": None,
            }
        )
        result = await client.start(request, deadline_ms=5_000)
        branch = getattr(result, "root", result)
        if getattr(branch, "ok", None) is not True:
            return None
        task_id = getattr(branch, "task_id", None)
        session_id = getattr(branch, "session_id", None)
        writer_id = getattr(branch, "writer_id", None)
        if type(task_id) is not str or type(session_id) is not str or type(writer_id) is not str:
            return None
        frontier = getattr(branch, "frontier", None)
        last_frontier = None
        if frontier is not None:
            sequence = getattr(frontier, "sequence", None)
            digest = getattr(frontier, "head_digest", None)
            if type(sequence) is str and type(digest) is str:
                from yoetz.adapters.integrations.codex_lifecycle import encode_frontier_token

                last_frontier = encode_frontier_token(sequence=sequence, head_digest=digest)
        mapping = mapping_from_start_ids(
            codex_session_id=codex_session_id,
            yoetz_task_id=task_id,
            yoetz_session_id=session_id,
            yoetz_writer_id=writer_id,
            last_frontier=last_frontier,
        )
        store_mapping(mapping, _state=_state)
        return mapping
    except Exception:
        return None
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()


def handle_observe(
    *,
    event_name: str,
    stdin_bytes: bytes | None = None,
    stdout: BinaryIO | None = None,
    workspace: str | None = None,
    _state: Path | None = None,
    connect: ServiceConnector | None = None,
    run_async: AsyncRunner | None = None,
    skip_service: bool = False,
    _entry_monotonic: float | None = None,
    _monotonic: Callable[[], float] = time.monotonic,
) -> int:
    """Bounded observation ingress for Codex lifecycle hooks. Always exits 0.

    ``skip_service`` keeps the hook fully local: capture, binding, and outbox
    enqueue still run, but no service connection is ever opened (auto-attach,
    mapped-session status, and outbox drains are all skipped).

    ``_entry_monotonic`` is the console shim's pre-import sample; without it the
    recorded import stage reads zero rather than guessing.
    """

    entry_started = _monotonic() if _entry_monotonic is None else _entry_monotonic
    stages: dict[str, int] = {}
    try:
        store = LocalObservationStore(_state=_state)
        stages["import"] = _elapsed_ms(entry_started, _monotonic())
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
                    record_hook_diagnostic("stdout_write_failed", event_name, _state=_state)
            if stream is None and sys.stdout is sys.__stdout__:
                with contextlib.suppress(BaseException):
                    sys.stdout.flush()
                    sys.stdout.close()
            return emitted

        payload = read_hook_payload(stdin_bytes)
        raw_event = event_name or payload.get("hook_event_name")
        if type(raw_event) is not str or not raw_event:
            _stdout_json({}, stdout)
            return 0
        resolved_event = raw_event
        try:
            capture_enabled = store.runtime_enabled()
        except Exception:
            record_hook_diagnostic("runtime_gate_unsafe", resolved_event, _state=_state)
            _stdout_json({}, stdout)
            return 0
        if not capture_enabled:
            # The READY generation synchronized an explicit disabled config.
            # Stop before session binding, ordinals, capture, or outbox enqueue.
            if resolved_event == "SessionStart":
                additional = ""
                with contextlib.suppress(Exception):
                    additional = _cached_recommendation_context(_state=_state)
                _stdout_json(
                    _context_output(resolved_event, additional) if additional else {}, stdout
                )
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
        if workspace is not None:
            try:
                # Resolve '.' / relative paths locally; never log or persist plaintext.
                workspace_locator = str(Path(workspace).expanduser().resolve(strict=False))
                workspace_commitment = store.workspace_commitment(workspace_locator)
                consent_probe = store.consent_for(workspace_commitment)
                if consent_probe is None or not consent_probe.active:
                    workspace_commitment = None
                    workspace_locator = None
            except Exception:
                workspace_commitment = None
                workspace_locator = None
        if workspace_commitment is None:
            # Bind only via an existing Codex-session→workspace map for this session.
            # Never guess a single "active" workspace across consented projects.
            workspace_commitment = store.find_workspace_for_codex_session(codex_session_id)
            workspace_locator = None
        consent = None if workspace_commitment is None else store.consent_for(workspace_commitment)
        if consent is None or not consent.active:
            # Consent missing/paused/revoked: no ingest, no spool; still exit 0.
            _stdout_json({}, stdout)
            return 0

        assert workspace_commitment is not None
        store_started = _monotonic()
        # One flush for the whole local pass. The batch is closed before any
        # service RPC so an outbox acknowledgement can never become durable
        # ahead of the ingest it acknowledges, and it never spans a network
        # wait: it holds the interprocess store lock for its duration.
        with store.batched(workspace_commitment):
            session_commitment = store.bind_codex_session(workspace_commitment, codex_session_id)
            source_generation = (
                store.begin_session_generation(workspace_commitment, session_commitment)
                if resolved_event == "SessionStart"
                else store.current_session_generation(workspace_commitment, session_commitment)
            )
            gap_codes: list[str] = []

            # Pair pre/post via correlation_id when present.
            correlation = _token_or_none(payload.get("correlation_id")) or _token_or_none(
                payload.get("tool_call_id")
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
            )
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
            if local_result.disposition.value == "accepted":
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

            # Selective secondary stream reconciliation (path never persisted/disclosed).
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
        mapping: LifecycleMapping | None = load_mapping(codex_session_id, _state=_state)
        drain_started = _monotonic()
        if resolved_event == "SessionStart":
            source = payload.get("source")
            if source != "clear":
                # SessionStart-only status/attach helpers: they drag protocol.models
                # and service.client, which no other event needs (#242).
                from yoetz.cli.hooks import (
                    _active_context,  # pyright: ignore[reportPrivateUsage]
                    _read_status,  # pyright: ignore[reportPrivateUsage]
                )

                with acquire_session_lock(codex_session_id, _state=_state) as owned:
                    if owned:
                        mapping = load_mapping(codex_session_id, _state=_state)
                        if mapping is None and not skip_advice_loop and not skip_service:

                            async def _attach() -> LifecycleMapping | None:
                                return await _try_auto_start(codex_session_id, _state=_state)

                            mapping = cast(LifecycleMapping | None, _resolve_runner()(_attach))
                            if mapping is None:
                                additional = (
                                    "Yoetz observation is consented for this workspace; "
                                    "no ledger task is mapped yet (observation-derived binding "
                                    "only). Call start to attach a task."
                                )
                            else:
                                additional = _active_context(mapping, mapping.last_frontier)
                        elif mapping is not None and not skip_service:
                            active_mapping = mapping

                            async def _status() -> object:
                                connector = cast(
                                    "hooks_cli.ServiceConnector",
                                    connect if connect is not None else _connect_service(),
                                )
                                return await _read_status(active_mapping, connect=connector)

                            kind, updated = cast(
                                tuple[str, LifecycleMapping | None], _resolve_runner()(_status)
                            )
                            if kind == "active" and updated is not None:
                                store_mapping(updated, _state=_state)
                                mapping = updated
                                additional = _active_context(updated, updated.last_frontier)
                            elif kind == "locked":
                                additional = (
                                    "Yoetz vault is locked for this mapped session; "
                                    "no live receipt can be promised."
                                )
                            else:
                                additional = (
                                    "Yoetz service is unavailable for this mapped session; "
                                    "no live receipt can be promised."
                                )
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

        stages["drain"] = _elapsed_ms(drain_started, _monotonic())

        # Advice selection and commit are serialized with the bounded stdout write. This keeps
        # concurrent hook processes from selecting the same identity while preserving the
        # commit-after-emit invariant: a failed write never suppresses a later delivery.
        pending_delivery: AdviceDelivery | None = None
        delivery_session_id: str | None = None
        delivery_eligible = (
            not additional and resolved_event in ADVICE_SAFE_EVENTS and not skip_advice_loop
        )
        delivery_gate = (
            store.batched(workspace_commitment) if delivery_eligible else contextlib.nullcontext()
        )
        with delivery_gate:
            if delivery_eligible:
                delivery_session_id = None if mapping is None else mapping.yoetz_session_id
                delivery = store.peek_advice_for_delivery(
                    workspace_commitment,
                    yoetz_session_id=delivery_session_id,
                    allow_standing=resolved_event in STANDING_ADVICE_CADENCE_EVENTS,
                )
                if delivery is not None:
                    additional = delivery.text[:_MAX_ADVICE_CONTEXT]
                    pending_delivery = delivery

            # Release recommendations are read from one bounded local cache only.
            # Existing task/receipt advice always wins this shared context channel.
            if not additional and resolved_event == "SessionStart" and not skip_advice_loop:
                with contextlib.suppress(Exception):
                    additional = _cached_recommendation_context(_state=_state)

            if additional:
                emitted = _stdout_json(_context_output(resolved_event, additional), stdout)
            else:
                emitted = _stdout_json({}, stdout)
            if emitted and pending_delivery is not None:
                # Strictly after the write: delivered-but-unrecorded costs one
                # redelivery, recorded-but-undelivered would cost the advice.
                # Nothing past the emission may raise — the outer handler would
                # write a second JSON object onto a stream that already has one.
                with contextlib.suppress(BaseException):
                    store.commit_advice_delivery(
                        workspace_commitment,
                        pending_delivery.delivery_identity,
                        yoetz_session_id=delivery_session_id,
                    )
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
