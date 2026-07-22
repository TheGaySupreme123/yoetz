"""CLI controls for live Codex observation consent, status, and stream reconcile."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import typer

from yoetz.adapters.integrations.codex_session_stream import (
    SessionStreamReader,
    default_stream_profile,
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
    status = store.status(ObservationStatusQuery(commitment))
    consent_label = (
        "absent"
        if consent is None
        else (
            "revoked"
            if consent.revoked_at is not None
            else ("paused" if consent.paused else "active")
        )
    )
    if json_output:
        _emit(
            {
                "workspace_commitment": commitment,
                "consent": consent_label,
                "status": observation_status_to_json(status),
            },
            json_output=True,
        )
        return 0
    _emit(
        {
            "workspace_commitment": commitment,
            "consent": consent_label,
            "lifecycle": status.lifecycle.value,
            "advice_frontier": status.advice_frontier or "none",
            "gaps": ",".join(status.gaps) if status.gaps else "none",
            "hook_coverage": str(status.source_coverage.get(ObservationSource.CODEX_HOOK, False)),
            "stream_coverage": str(
                status.source_coverage.get(ObservationSource.CODEX_SESSION_STREAM, False)
            ),
        },
        json_output=False,
    )
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
    session_commitment = store.session_commitment(session_token[:128])
    store.bind_session(workspace_commitment, session_commitment)
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
    payload: dict[str, JsonValue] = {
        "accepted": accepted,
        "duplicates": duplicates,
        "gaps": advance.gaps,
        "byte_position": advance.cursor.byte_position,
        "event_position": advance.cursor.event_position,
        "generation": advance.cursor.source_generation,
        "rotated": advance.rotated,
        "truncated": advance.truncated,
    }
    _emit(payload, json_output=json_output)
    return 0
