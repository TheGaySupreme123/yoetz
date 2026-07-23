"""CLI controls for live Codex observation consent, status, and stream reconcile."""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import typer

from yoetz.adapters.approved_checks import ApprovedCheckRunner
from yoetz.adapters.git_subject_state import GitSubjectStateAdapter, open_local_workspace
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
from yoetz.application.observation_check_policy import load_observation_check_policy
from yoetz.application.observation_verification import run_bound_approved_check
from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationCursor,
    ObservationGapCode,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatusQuery,
    observation_status_to_json,
)
from yoetz.ports.subject_state import SubjectStateCaptureCommand, SubjectStateFormat
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import PublicOperationError

__all__ = [
    "grant_observation",
    "observe_checks_preview",
    "observe_checks_revoke",
    "observe_checks_run",
    "observe_checks_status",
    "observe_checks_trust",
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


def _emit(payload: Mapping[str, JsonValue], *, json_output: bool) -> None:
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
            "top_evidence": None if top is None or not top.evidence_refs else top.evidence_refs[0],
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
        "recommendation": ("none" if snapshot is None else snapshot.recommended_next_action),
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


def _check_policy_context(workspace: str | None, *, _state: Path | None):
    root = _resolve_workspace(workspace)
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(root))
    policy, _raw = load_observation_check_policy(root)
    return root, commitment, store, policy


def observe_checks_preview(
    *, workspace: str | None, json_output: bool, _state: Path | None = None
) -> int:
    try:
        _root, commitment, store, policy = _check_policy_context(workspace, _state=_state)
    except Exception:
        typer.echo("observation_checks_preview_failed:invalid_policy", err=True)
        return 20
    checks = tuple(
        {
            "id": item.approval_id,
            "argv": item.argv,
            "timeout_seconds": int(item.timeout_seconds),
            "network": item.allow_network,
            "approval_commitment": item.approval_commitment,
        }
        for item in policy.checks
    )
    _emit(
        {
            "workspace_commitment": commitment,
            "policy_digest": policy.raw_digest,
            "trusted": store.policy_digest_is_trusted(commitment, policy.raw_digest),
            "checks": checks,
        },
        json_output=json_output,
    )
    return 0


def observe_checks_trust(
    *,
    workspace: str | None,
    policy_digest: str,
    _state: Path | None = None,
) -> int:
    try:
        _root, commitment, store, policy = _check_policy_context(workspace, _state=_state)
    except Exception:
        typer.echo("observation_checks_trust_failed:invalid_policy", err=True)
        return 20
    if policy_digest != policy.raw_digest:
        typer.echo("observation_checks_trust_failed:digest_mismatch", err=True)
        return 20
    store.trust_policy_digest(commitment, policy.raw_digest)
    typer.echo(f"observation_checks_trusted:{policy.raw_digest}")
    return 0


def observe_checks_status(
    *, workspace: str | None, json_output: bool, _state: Path | None = None
) -> int:
    try:
        _root, commitment, store, policy = _check_policy_context(workspace, _state=_state)
    except Exception:
        typer.echo("observation_checks_status_failed:invalid_policy", err=True)
        return 20
    trusted = store.policy_digest_is_trusted(commitment, policy.raw_digest)
    _emit(
        {
            "workspace_commitment": commitment,
            "policy_digest": policy.raw_digest,
            "state": "trusted" if trusted else "untrusted",
            "executable_checks": (
                tuple(item.approval_id for item in policy.checks if not item.allow_network)
                if trusted
                else ()
            ),
            "network_check_state": (
                ObservationGapCode.NETWORK_CHECK_UNSUPPORTED.value
                if any(item.allow_network for item in policy.checks)
                else "not_requested"
            ),
        },
        json_output=json_output,
    )
    return 0


def observe_checks_revoke(*, workspace: str | None, _state: Path | None = None) -> int:
    root = _resolve_workspace(workspace)
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(root))
    store.revoke_policy_trust(commitment)
    typer.echo("observation_checks_revoked")
    return 0


def observe_checks_run(
    *, workspace: str | None, json_output: bool, _state: Path | None = None
) -> int:
    try:
        root, commitment, store, policy = _check_policy_context(workspace, _state=_state)
        if not store.policy_digest_is_trusted(commitment, policy.raw_digest):
            store.note_coverage_gap(commitment, ObservationGapCode.POLICY_UNTRUSTED.value)
            typer.echo("observation_checks_run_failed:policy_untrusted", err=True)
            return 20
        handle = open_local_workspace(root)
        capture = GitSubjectStateAdapter()

        def subject_digest(_handle: object) -> str:
            result = capture.capture(
                SubjectStateCaptureCommand(handle, SubjectStateFormat.GIT_STRUCTURAL_V1)
            )
            if result.subject_state is None:
                raise ValueError("subject_state_unavailable")
            return canonical_digest(
                {
                    "tree_digest": result.subject_state.tree_digest,
                    "diff_digest": result.subject_state.diff_digest,
                }
            )

        expected = subject_digest(handle)
        runner = ApprovedCheckRunner({item.approval_commitment: item for item in policy.checks})
        results: list[dict[str, JsonValue]] = []
        for index, approval in enumerate(policy.checks, 1):
            result, fact = run_bound_approved_check(
                runner=runner,
                workspace=handle,
                approval=approval,
                expected_subject_state_digest=expected,
                capture_subject_state=subject_digest,
                cursor_event_position=index,
            )
            if result.outcome.value == "network_denied":
                store.note_coverage_gap(
                    commitment, ObservationGapCode.NETWORK_CHECK_UNSUPPORTED.value
                )
            results.append(
                {
                    "id": approval.approval_id,
                    "status": result.status.value,
                    "outcome": result.outcome.value,
                    "result_digest": result.result_digest,
                    "output_digest": result.output_digest,
                    "output_bytes": result.output_bytes,
                    "is_current": False if fact is None else fact.is_current,
                }
            )
        _emit(
            {
                "workspace_commitment": commitment,
                "policy_digest": policy.raw_digest,
                "subject_state_digest": expected,
                "results": tuple(results),
            },
            json_output=json_output,
        )
        return 0 if all(item["status"] == "passed" for item in results) else 20
    except Exception:
        typer.echo("observation_checks_run_failed:unavailable", err=True)
        return 20


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
    overflow = False
    committed_cursor = existing
    for envelope in advance.envelopes:
        result = store.ingest(envelope)
        if result.disposition.value not in {"accepted", "duplicate"}:
            break
        if (
            store.enqueue_outbox(workspace_commitment, session_token[:128], envelope)
            == ObservationGapCode.OUTBOX_OVERFLOW.value
        ):
            overflow = True
            break
        committed_cursor = envelope.cursor
        if result.disposition.value == "accepted":
            accepted += 1
        else:
            duplicates += 1
    if not overflow:
        committed_cursor = advance.cursor
    store.set_stream_cursor(workspace_commitment, session_commitment, committed_cursor)
    store.set_stream_partial(
        workspace_commitment,
        session_commitment,
        advance.partial_line if not overflow else b"",
    )
    gaps = advance.gaps
    if overflow and ObservationGapCode.OUTBOX_OVERFLOW.value not in gaps:
        gaps = (*gaps, ObservationGapCode.OUTBOX_OVERFLOW.value)
    payload = {
        "accepted": accepted,
        "duplicates": duplicates,
        "gaps": gaps,
        "byte_position": committed_cursor.byte_position,
        "event_position": committed_cursor.event_position,
        "generation": committed_cursor.source_generation,
        "rotated": advance.rotated,
        "truncated": advance.truncated,
        "resolved": True,
        "mode": "recovery_explicit_path",
    }
    _emit(payload, json_output=json_output)
    return 0
