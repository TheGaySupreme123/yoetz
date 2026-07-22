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
from yoetz.cli.hooks import (
    _active_context,
    _context_output,
    _read_status,
    _stderr_line,
    _stdout_json,
    read_hook_payload,
)
from yoetz.domain.observation import (
    AdviceSnapshot,
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
    observation_envelope_to_json,
)
from yoetz.domain.values import JsonObject, JsonValue, Timestamp, timestamp_from_datetime
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.canonical import canonical_digest
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
    }
)
_TOKEN_CHARS: Final = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/+-"
)


type AsyncRunner = Callable[[Callable[[], Awaitable[object]]], object]
type ServiceConnector = Callable[[ControlClientKind], Awaitable[object]]


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
    ):
        token = _token_or_none(payload.get(key))
        if token is not None and key in _STRUCTURAL_ALLOW:
            fields[key] = token
    # Nested tool_input / tool_response never contribute prose — only structural scalars.
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, Mapping):
        nested = cast(Mapping[str, JsonValue], tool_input)
        for key in ("tool_name", "permission_kind"):
            token = _token_or_none(nested.get(key))
            if token is not None and key not in fields:
                fields[key] = token
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
    event_name: str, payload: Mapping[str, JsonValue], structural: JsonObject
) -> str:
    material = JsonObject(
        {
            "event_kind": event_name,
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


def _event_ordinal(payload: Mapping[str, JsonValue]) -> int:
    raw = payload.get("event_ordinal")
    if type(raw) is int and not isinstance(raw, bool) and raw >= 1:
        return raw
    return 1


def map_hook_payload_to_envelope(
    event_name: str,
    payload: Mapping[str, JsonValue],
    *,
    session_commitment: str,
    event_ordinal: int,
    gap_codes: tuple[str, ...] = (),
) -> ObservationEnvelope:
    """Map a bounded hook payload to a structural ObservationEnvelope."""

    if event_name not in SUPPORTED_HOOK_EVENTS:
        structural = JsonObject({"hook_name": "unsupported"})
        gaps = tuple(
            sorted({*gap_codes, ObservationGapCode.UNSUPPORTED_EVENT.value}, key=str.encode)
        )
        identity = _source_identity(event_name, payload, structural)
        return ObservationEnvelope(
            session_commitment=session_commitment,
            event_kind=_token_or_none(event_name) or "unsupported_event",
            source_identity=identity,
            source=ObservationSource.CODEX_HOOK,
            cursor=ObservationCursor(
                source_generation=1,
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
    identity = _source_identity(event_name, payload, structural)
    commitment = (
        f"hmac-sha256:{canonical_digest(JsonObject({'id': identity})).removeprefix('sha256:')}"
    )
    return ObservationEnvelope(
        session_commitment=session_commitment,
        event_kind=event_name,
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            source_generation=1,
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


def _advice_context(snapshot: AdviceSnapshot) -> str:
    findings = ",".join(str(item) for item in snapshot.ranked_finding_ids[:8])
    text = (
        f"Yoetz advice frontier {snapshot.freshness_frontier}: "
        f"next={snapshot.recommended_next_action}; findings={findings}."
    )
    return text[:_MAX_ADVICE_CONTEXT]


async def _try_service_ingest(envelope: ObservationEnvelope) -> str | None:
    """Attempt service ingest; return gap code on soft failure, None on success/skip."""

    client = None
    try:
        client = await connect_service(ControlClientKind.CLI)
        await client.observation_ingest(observation_envelope_to_json(envelope), deadline_ms=3_000)
        return None
    except ControlError as error:
        if error.reason == "vault_locked":
            return ObservationGapCode.VAULT_LOCKED.value
        return ObservationGapCode.SERVICE_UNAVAILABLE.value
    except Exception:
        return ObservationGapCode.SERVICE_UNAVAILABLE.value
    finally:
        if client is not None:
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
    try:
        payload = read_hook_payload(stdin_bytes)
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
        if workspace is not None:
            try:
                workspace_commitment = store.workspace_commitment(str(Path(workspace).resolve()))
            except Exception:
                workspace_commitment = None
        if workspace_commitment is None:
            workspace_commitment = store.find_workspace_for_codex_session(codex_session_id)
        consent = None if workspace_commitment is None else store.consent_for(workspace_commitment)
        if consent is None or not consent.active:
            # Consent missing/paused/revoked: no ingest, no spool; still exit 0.
            _stdout_json({}, stdout)
            return 0

        assert workspace_commitment is not None
        session_commitment = store.bind_codex_session(workspace_commitment, codex_session_id)
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

        envelope = map_hook_payload_to_envelope(
            resolved_event if resolved_event in SUPPORTED_HOOK_EVENTS else "unsupported_event",
            payload,
            session_commitment=session_commitment,
            event_ordinal=_event_ordinal(payload),
            gap_codes=tuple(sorted(set(gap_codes), key=str.encode)),
        )

        # Local durable ingest first (never plaintext transcript spool).
        local_result = store.ingest(envelope)
        _ = local_result

        if not skip_service:

            async def _ingest() -> str | None:
                return await _try_service_ingest(envelope)

            service_gap = cast(str | None, runner(_ingest))
            if service_gap is not None:
                _stderr_line(f"hook_observe_degraded: {service_gap}")
                # Record structural gap without retaining payload prose.
                gap_envelope = ObservationEnvelope(
                    session_commitment=session_commitment,
                    event_kind="observation_gap",
                    source_identity=f"hook:gap:{service_gap}:{envelope.source_identity[-24:]}",
                    source=ObservationSource.CODEX_HOOK,
                    cursor=ObservationCursor(
                        source_generation=envelope.cursor.source_generation,
                        byte_position=envelope.cursor.byte_position,
                        event_position=envelope.cursor.event_position + 1,
                        last_source_commitment=envelope.cursor.last_source_commitment,
                        mapping_version=HOOK_MAPPING_VERSION,
                    ),
                    receipt_time=_now(),
                    structural_payload=JsonObject({"hook_name": resolved_event}),
                    content_object_refs=(),
                    gap_codes=(service_gap,),
                )
                with contextlib.suppress(Exception):
                    store.ingest(gap_envelope)

        # SessionStart auto-attach for consented workspaces.
        additional = ""
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

                            async def _status() -> object:
                                return await _read_status(
                                    mapping, connect=connect or connect_service
                                )

                            kind, updated = cast(
                                tuple[str, LifecycleMapping | None], runner(_status)
                            )
                            if kind == "active" and updated is not None:
                                store_mapping(updated, _state=_state)
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
