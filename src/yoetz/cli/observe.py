"""CLI controls for live Codex observation consent, status, and stream reconcile."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Final

import typer

from yoetz.adapters.integrations.codex_session_stream import (
    CodexSessionStreamLocator,
    SessionStreamReader,
    default_stream_profile,
    resolve_codex_home,
)
from yoetz.adapters.integrations.codex_session_stream import (
    reconcile_session_stream as advance_session_stream,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    LocalObservationStore,
)
from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationCursor,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatusQuery,
    observation_status_to_json,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.errors import PublicOperationError

__all__ = [
    "grant_observation",
    "observe_status",
    "pause_observation",
    "reconcile_session_stream",
    "resume_observation",
    "revoke_observation",
]

_EMPTY_COMMITMENT: Final = "hmac-sha256:" + ("0" * 64)


def _resolve_workspace(path: str | None) -> Path:
    root = Path.cwd() if path is None else Path(path)
    return root.resolve()


def _emit(payload: dict[str, JsonValue], *, json_output: bool) -> None:
    if json_output:
        typer.echo(canonical_encode(payload).decode("utf-8"))
        return
    for key, value in payload.items():
        typer.echo(f"{key}: {value}")


def observe_status(
    *,
    workspace: str | None,
    json_output: bool,
    _state: Path | None = None,
) -> int:
    store = LocalObservationStore(_state=_state)
    root = _resolve_workspace(workspace)
    commitment = store.workspace_commitment(str(root))
    consent = store.consent_for(commitment)
    with contextlib.suppress(Exception):
        store.refresh_advice(commitment)
    status = store.status(ObservationStatusQuery(commitment))
    snapshot = store.advice_snapshot_for(commitment)
    consent_label = (
        "absent"
        if consent is None
        else (
            "revoked"
            if consent.revoked_at is not None
            else ("paused" if consent.paused else "active")
        )
    )
    advice_payload: dict[str, JsonValue]
    if snapshot is None:
        advice_payload = {"present": False}
    else:
        top = snapshot.ranked_items[0] if snapshot.ranked_items else None
        advice_payload = {
            "present": True,
            "recommended_next_action": snapshot.recommended_next_action,
            "freshness_frontier": snapshot.freshness_frontier,
            "top_summary": None if top is None else top.summary,
            "top_detail": None if top is None else top.detail,
            "top_evidence": None
            if top is None or not top.evidence_refs
            else top.evidence_refs[0],
            "finding_ids": tuple(str(item) for item in snapshot.ranked_finding_ids[:8]),
        }
    if json_output:
        _emit(
            {
                "workspace_commitment": commitment,
                "consent": consent_label,
                "status": observation_status_to_json(status),
                "advice": advice_payload,
            },
            json_output=True,
        )
        return 0
    payload: dict[str, JsonValue] = {
        "workspace_commitment": commitment,
        "consent": consent_label,
        "lifecycle": status.lifecycle.value,
        "lag_events": status.lag_events,
        "advice_frontier": status.advice_frontier or "none",
        "gaps": ",".join(status.gaps) if status.gaps else "none",
        "hook_coverage": str(status.source_coverage.get(ObservationSource.CODEX_HOOK, False)),
        "stream_coverage": str(
            status.source_coverage.get(ObservationSource.CODEX_SESSION_STREAM, False)
        ),
        "recommendation": (
            "none"
            if snapshot is None
            else snapshot.recommended_next_action
        ),
        "advice_summary": (
            "none"
            if snapshot is None or not snapshot.ranked_items
            else snapshot.ranked_items[0].summary
        ),
    }
    _emit(payload, json_output=False)
    return 0


def grant_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    root = _resolve_workspace(workspace)
    commitment = store.workspace_commitment(str(root))
    store.grant_consent(commitment)
    # Never log the raw path — only the commitment.
    typer.echo(f"observation_consent_granted:{commitment}")
    return 0


def pause_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(_resolve_workspace(workspace)))
    try:
        status = store.pause(ObservationControlCommand(commitment))
    except PublicOperationError as error:
        typer.echo(f"observation_pause_failed:{error.code.value}", err=True)
        return 20
    typer.echo(f"observation_paused:{status.lifecycle.value}")
    return 0


def resume_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(_resolve_workspace(workspace)))
    try:
        status = store.resume(ObservationControlCommand(commitment))
    except PublicOperationError as error:
        typer.echo(f"observation_resume_failed:{error.code.value}", err=True)
        return 20
    typer.echo(f"observation_resumed:{status.lifecycle.value}")
    return 0


def revoke_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(_resolve_workspace(workspace)))
    try:
        status = store.revoke(ObservationRevokeCommand(commitment, retain_evidence=True))
    except PublicOperationError as error:
        typer.echo(f"observation_revoke_failed:{error.code.value}", err=True)
        return 20
    typer.echo(f"observation_revoked:{status.lifecycle.value}:evidence_retained")
    return 0


def reconcile_session_stream(
    *,
    session_file: str,
    workspace: str | None,
    json_output: bool,
    _state: Path | None = None,
) -> int:
    """Manual recovery/diagnostic reconcile; automatic reconcile is hook-driven."""

    store = LocalObservationStore(_state=_state)
    root = _resolve_workspace(workspace)
    workspace_commitment = store.workspace_commitment(str(root))
    consent = store.consent_for(workspace_commitment)
    if consent is None or not consent.active:
        typer.echo("observation_reconcile_failed:consent_missing", err=True)
        return 20
    path = Path(session_file)
    if not path.is_file() or path.is_symlink():
        typer.echo("observation_reconcile_failed:session_file_unreadable", err=True)
        return 20
    # Session commitment is derived from the file's stem (opaque token), never the full path.
    session_token = path.stem if path.stem else "session"
    if "-" in session_token:
        session_token = session_token.rsplit("-", 1)[-1] or session_token
    session_commitment = store.session_commitment(session_token[:128])
    store.bind_session(workspace_commitment, session_commitment)
    locator = CodexSessionStreamLocator(resolve_codex_home())
    validated = locator.resolve(session_id=session_token[:128], hook_provided_path=str(path))
    if validated is not None:
        payload = advance_session_stream(
            store,
            workspace_commitment=workspace_commitment,
            session_commitment=session_commitment,
            codex_session_id=session_token[:128],
            locator=locator,
            hook_provided_path=str(validated),
        )
        payload = {**payload, "mode": "locator"}
        _emit(payload, json_output=json_output)
        return 0
    # Recovery mode for explicit paths outside the selected Codex home.
    existing = store.get_stream_cursor(workspace_commitment, session_commitment)
    if existing is None:
        existing = ObservationCursor(
            source_generation=1,
            byte_position=0,
            event_position=0,
            last_source_commitment=_EMPTY_COMMITMENT,
            mapping_version=STREAM_MAPPING_VERSION,
        )
    reader = SessionStreamReader(
        session_commitment=session_commitment,
        profile=default_stream_profile(),
        cursor=existing,
        key_material=store.key_material(),
        partial_line=store.get_stream_partial(workspace_commitment, session_commitment),
    )
    advance = reader.advance(path)
    accepted = 0
    duplicates = 0
    for envelope in advance.envelopes:
        result = store.ingest(envelope)
        if result.disposition.value == "accepted":
            accepted += 1
        elif result.disposition.value == "duplicate":
            duplicates += 1
    store.set_stream_cursor(workspace_commitment, session_commitment, advance.cursor)
    store.set_stream_partial(workspace_commitment, session_commitment, advance.partial_line)
    payload = {
        "accepted": accepted,
        "duplicates": duplicates,
        "gaps": advance.gaps,
        "byte_position": advance.cursor.byte_position,
        "event_position": advance.cursor.event_position,
        "generation": advance.cursor.source_generation,
        "rotated": advance.rotated,
        "truncated": advance.truncated,
        "resolved": True,
        "mode": "recovery_explicit_path",
    }
    _emit(payload, json_output=json_output)
    return 0
