"""Unified Codex hook observation ingress (structural envelopes only)."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Final, cast

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
    LocalObservationStore,
)
from yoetz.cli import hooks as hooks_cli
from yoetz.domain.observation import (
    AdviceSnapshot,
    ObservationContentChunk,
    ObservationContentKind,
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationSource,
    hook_source_commitment,
    observation_ingest_request_to_json,
    observation_ingest_result_from_json,
)
from yoetz.domain.values import JsonObject, Timestamp, timestamp_from_datetime
from yoetz.observability.privacy import redact_sensitive_content
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import StartRequest
from yoetz.service.client import connect_service

__all__ = [
    "ADVICE_SAFE_EVENTS",
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
_MAX_ADVICE_CONTEXT: Final = 1_200
_MAX_CONTENT_CHUNK: Final = 256 * 1024
# Ingest rejections that are recoverable: keep the outbox entry pending for a
# later drain. Anything else is permanently invalid and gets quarantined so it
# is never silently dropped as if committed.
_RETRYABLE_INGEST_REJECTIONS: Final = frozenset(
    {
        ObservationGapCode.SERVICE_UNAVAILABLE.value,
        ObservationGapCode.VAULT_LOCKED.value,
        ObservationGapCode.MAPPING_MISSING.value,
        "paused",
    }
)
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
) -> tuple[ObservationContentChunk, ...]:
    """Extract only explicitly visible task content and redact it before transport."""

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
    for kind, label, raw in selected:
        redacted, detected = redact_sensitive_content(raw)
        if not redacted:
            continue
        redacted = redacted[:remaining]
        if not redacted:
            break
        parts = [
            redacted[offset : offset + _MAX_CONTENT_CHUNK]
            for offset in range(0, len(redacted), _MAX_CONTENT_CHUNK)
        ][:16]
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
                return tuple(chunks)
        remaining -= len(redacted)
    return tuple(chunks)


def _advice_context(snapshot: AdviceSnapshot) -> str:
    from yoetz.application.observation_advice import hook_advice_context

    return hook_advice_context(snapshot)[:_MAX_ADVICE_CONTEXT]


async def _try_service_ingest(
    codex_session_id: str,
    envelope: ObservationEnvelope,
    *,
    content_chunks: tuple[ObservationContentChunk, ...] = (),
) -> tuple[str | None, str | None]:
    """Attempt service ingest.

    Returns ``(soft_fail_gap, rejected_reason)``. Soft-fail gaps are transport/vault
    problems. Rejected reasons come from a successful RPC that returned ``rejected``.
    Both ``accepted`` and ``duplicate`` clear the outbox (idempotent success).
    """

    client = None
    try:
        client = await connect_service(ControlClientKind.CLI)
        body = observation_ingest_request_to_json(
            ObservationIngestRequest(
                codex_session_id=codex_session_id,
                envelope=envelope,
                content_chunks=content_chunks,
            )
        )
        raw = await client.observation_ingest(body, deadline_ms=3_000)
        try:
            result = observation_ingest_result_from_json(raw)
        except ProtocolValueError, TypeError, ValueError:
            return ObservationGapCode.SERVICE_UNAVAILABLE.value, None
        if result.disposition is ObservationIngestDisposition.REJECTED:
            reason = result.reason or ObservationGapCode.SERVICE_UNAVAILABLE.value
            return None, reason
        return None, None
    except ControlError as error:
        if error.reason == "vault_locked":
            return ObservationGapCode.VAULT_LOCKED.value, None
        return ObservationGapCode.SERVICE_UNAVAILABLE.value, None
    except Exception:
        return ObservationGapCode.SERVICE_UNAVAILABLE.value, None
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()


async def _drain_outbox(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    codex_session_id: str,
    content_by_source_identity: Mapping[str, tuple[ObservationContentChunk, ...]] | None = None,
) -> None:
    """Drain all mapped-session work fairly; ack only after service commit.

    The current session receives the first slot for low-latency hook feedback,
    then sessions are interleaved round-robin. A busy current session therefore
    cannot indefinitely filter or starve recovered work from another mapped
    session in the same workspace.
    """

    all_pending = store.list_pending_outbox(workspace_commitment)
    grouped: dict[str, list[ObservationEnvelope]] = {}
    for session_id, envelope in all_pending:
        grouped.setdefault(session_id, []).append(envelope)
    session_order = sorted(grouped, key=str.encode)
    if codex_session_id in grouped:
        session_order.remove(codex_session_id)
        session_order.insert(0, codex_session_id)
    pending: list[tuple[str, ObservationEnvelope]] = []
    while grouped and len(pending) < 64:
        for session_id in tuple(session_order):
            queue = grouped.get(session_id)
            if not queue:
                grouped.pop(session_id, None)
                continue
            pending.append((session_id, queue.pop(0)))
            if not queue:
                grouped.pop(session_id, None)
            if len(pending) >= 64:
                break
    for session_id, envelope in pending:
        chunks = (
            ()
            if content_by_source_identity is None
            else content_by_source_identity.get(envelope.source_identity, ())
        )
        if chunks:
            soft_fail, rejected = await _try_service_ingest(
                session_id, envelope, content_chunks=chunks
            )
        else:
            soft_fail, rejected = await _try_service_ingest(session_id, envelope)
        if soft_fail is not None:
            # Transport/vault problem: retryable, keep the entry pending.
            store.note_coverage_gap(workspace_commitment, soft_fail)
            if chunks:
                # Plaintext chunks are intentionally not spooled. Structural
                # replay remains pending and the omission is explicit.
                store.note_coverage_gap(
                    workspace_commitment,
                    ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
                )
            continue
        if rejected is not None:
            store.note_coverage_gap(workspace_commitment, rejected)
            if rejected in _RETRYABLE_INGEST_REJECTIONS:
                # Recoverable (service, vault, mapping, paused): keep pending.
                continue
            # Permanently invalid: move to a bounded, visible quarantine.
            # Never acknowledge as committed — that would silently drop the row.
            store.quarantine_outbox(
                workspace_commitment, session_id, envelope.source_identity, rejected
            )
            continue
        # accepted / duplicate: coordinator reconciled the durable ledger.
        store.acknowledge_outbox(workspace_commitment, session_id, envelope.source_identity)


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

    client = None
    try:
        client = await connect_service(ControlClientKind.CLI)
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
) -> int:
    """Bounded observation ingress for Codex lifecycle hooks. Always exits 0."""

    import anyio

    runner: AsyncRunner = cast(AsyncRunner, anyio.run if run_async is None else run_async)
    store = LocalObservationStore(_state=_state)
    # Shared hook IO/status helpers live in cli.hooks; intentional private seam reuse.
    _stderr_line = hooks_cli._stderr_line  # pyright: ignore[reportPrivateUsage]
    _stdout_json = hooks_cli._stdout_json  # pyright: ignore[reportPrivateUsage]
    _context_output = hooks_cli._context_output  # pyright: ignore[reportPrivateUsage]
    _active_context = hooks_cli._active_context  # pyright: ignore[reportPrivateUsage]
    _read_status = hooks_cli._read_status  # pyright: ignore[reportPrivateUsage]
    try:
        payload = hooks_cli.read_hook_payload(stdin_bytes)
        raw_event = event_name or payload.get("hook_event_name")
        if type(raw_event) is not str or not raw_event:
            _stdout_json({}, stdout)
            return 0
        resolved_event = raw_event
        session_raw = payload.get("session_id")
        try:
            codex_session_id = validate_codex_session_id(session_raw)
        except ProtocolValueError:
            _stderr_line("hook_observe_degraded: invalid_session")
            _stdout_json({}, stdout)
            return 0

        workspace_commitment: str | None = None
        workspace_locator: str | None = None
        if workspace is not None:
            try:
                workspace_locator = str(Path(workspace).resolve())
                workspace_commitment = store.workspace_commitment(workspace_locator)
            except Exception:
                workspace_commitment = None
                workspace_locator = None
        if workspace_commitment is None:
            workspace_commitment = store.find_workspace_for_codex_session(codex_session_id)
        consent = None if workspace_commitment is None else store.consent_for(workspace_commitment)
        if consent is None or not consent.active:
            # Consent missing/paused/revoked: no ingest, no spool; still exit 0.
            _stdout_json({}, stdout)
            return 0

        assert workspace_commitment is not None
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
        content_chunks = _visible_content_chunks(
            resolved_event,
            payload,
            envelope=envelope,
            workspace_locator=workspace_locator,
        )
        content_map = (
            {envelope.source_identity: content_chunks} if content_chunks else None
        )

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
        with contextlib.suppress(Exception):
            store.refresh_advice(workspace_commitment)

        # SessionStart: auto-start/attach first, persist mapping, then drain outbox.
        additional = ""
        mapping: LifecycleMapping | None = load_mapping(codex_session_id, _state=_state)
        if resolved_event == "SessionStart":
            source = payload.get("source")
            if source != "clear":
                with acquire_session_lock(codex_session_id, _state=_state) as owned:
                    if owned:
                        mapping = load_mapping(codex_session_id, _state=_state)
                        if mapping is None and not skip_advice_loop:

                            async def _attach() -> LifecycleMapping | None:
                                return await _try_auto_start(codex_session_id, _state=_state)

                            mapping = cast(LifecycleMapping | None, runner(_attach))
                            if mapping is None:
                                additional = (
                                    "Yoetz observation is consented for this workspace; "
                                    "no ledger task is mapped yet (observation-derived binding "
                                    "only). Call start to attach a task."
                                )
                            else:
                                additional = _active_context(mapping, mapping.last_frontier)
                        elif mapping is not None:
                            active_mapping = mapping

                            async def _status() -> object:
                                connector: hooks_cli.ServiceConnector = (
                                    connect if connect is not None else connect_service
                                )
                                return await _read_status(active_mapping, connect=connector)

                            kind, updated = cast(
                                tuple[str, LifecycleMapping | None], runner(_status)
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
                        if mapping is not None and not skip_service:

                            async def _drain() -> None:
                                await _drain_outbox(
                                    store,
                                    workspace_commitment=workspace_commitment,
                                    codex_session_id=codex_session_id,
                                    content_by_source_identity=content_map,
                                )

                            with contextlib.suppress(Exception):
                                runner(_drain)

        if not skip_service and resolved_event != "SessionStart":
            # Every later mapped hook drains the complete session outbox, so the
            # current envelope plus any stream-recovered or previously-pending
            # entries all reconcile. Retryable rejections stay pending and
            # permanently-invalid ones are quarantined (never dropped) by the
            # shared drain routing. Unmapped events remain pending until mapped.
            if mapping is not None:

                async def _drain_all() -> None:
                    await _drain_outbox(
                        store,
                        workspace_commitment=workspace_commitment,
                        codex_session_id=codex_session_id,
                        content_by_source_identity=content_map,
                    )

                with contextlib.suppress(Exception):
                    runner(_drain_all)

        if content_chunks and (skip_service or mapping is None):
            # Content is intentionally ephemeral. Without a ready mapped
            # service there is no encrypted destination, so retain only the
            # structural envelope plus an explicit omission gap.
            store.note_coverage_gap(
                workspace_commitment,
                ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
            )

        # Nonblocking advice delivery at safe points (suppress Yoetz self-tool loops).
        if not additional and resolved_event in ADVICE_SAFE_EVENTS and not skip_advice_loop:
            snapshot = store.peek_advice_for_delivery(workspace_commitment)
            if snapshot is not None:
                additional = _advice_context(snapshot)

        if additional:
            _stdout_json(_context_output(resolved_event, additional), stdout)
        else:
            _stdout_json({}, stdout)
        return 0
    except Exception:
        _stderr_line("hook_observe_degraded: observe")
        try:
            _stdout_json({}, stdout)
        except Exception:
            pass
        return 0
