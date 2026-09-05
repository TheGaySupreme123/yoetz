"""CLI controls for live Codex observation consent, status, and stream reconcile."""

from __future__ import annotations

import contextlib
import errno
import functools
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, ParamSpec, Protocol, cast

import typer

from yoetz.adapters.approved_checks import ApprovedCheckRunner
from yoetz.adapters.git_subject_state import GitSubjectStateAdapter, open_local_workspace
from yoetz.adapters.integrations.codex_lifecycle import load_mapping
from yoetz.adapters.integrations.codex_marketplace import inspect_activation
from yoetz.adapters.integrations.codex_session_stream import (
    CodexSessionStreamLocator,
    reconcile_session_stream_path,
    resolve_codex_home,
)
from yoetz.adapters.integrations.codex_session_stream import (
    reconcile_session_stream as advance_session_stream,
)
from yoetz.adapters.integrations.observation_local import (
    LocalObservationStore,
    ObservationOutboxRow,
)
from yoetz.application.observation_check_policy import load_observation_check_policy
from yoetz.application.observation_drain import (
    EXPECTED_OBSERVATION_BACKPRESSURE_REASONS,
    WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS,
    ObservationDrainAction,
    route_observation_ingest,
)
from yoetz.application.observation_verification import run_bound_approved_check
from yoetz.cli.exits import exit_code_for, remediation_message
from yoetz.cli.hook_diagnostics import hook_diagnostic_summary
from yoetz.cli.workspace_binding import canonical_workspace_locator
from yoetz.config.paths import PathSafetyError
from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatusQuery,
    observation_ingest_request_to_json,
    observation_ingest_result_from_json,
    observation_status_to_json,
)
from yoetz.domain.values import JsonObject
from yoetz.domain.values import JsonValue as DomainJsonValue
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.ports.integrations import IntegrationScope, IntegrationTarget
from yoetz.ports.subject_state import SubjectStateCaptureCommand, SubjectStateFormat
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.service.client import connect_service_on_demand

__all__ = [
    "grant_observation",
    "drain_observation",
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

_SETUP_PROBE_SESSION: Final = "yoetz-setup-probe-session"
_DRAIN_DEADLINE_MS: Final = 3_000


class _DrainClient(Protocol):
    async def observation_ingest(
        self, body: DomainJsonValue, *, deadline_ms: int | None = None
    ) -> DomainJsonValue: ...

    async def close(self) -> None: ...


type DrainConnector = Callable[[ControlClientKind], Awaitable[_DrainClient]]


_WORKSPACE_LOCATOR_INVALID: Final = "workspace_locator_invalid"
_UNSAFE_OBSERVATION_STORAGE_ERRNOS: Final = frozenset(
    {
        errno.EISDIR,
        errno.ELOOP,
        errno.ENOTDIR,
    }
)
_P = ParamSpec("_P")


def _resolve_workspace(path: str | None) -> Path:
    root = canonical_workspace_locator("." if path is None else path)
    if root is None:
        raise ValueError(_WORKSPACE_LOCATOR_INVALID)
    return Path(root)


def _typed_failure(
    operation: str,
    reason: str,
    *,
    code: PublicErrorCode,
    message: str,
    retryable: bool,
    json_output: bool,
) -> int:
    """Report one bounded failure that names its layer, never `internal_error` (#428).

    The token comes first so existing machine-readable expectations hold; the
    remediation follows it. JSON callers get the same facts as one object.
    """

    if json_output:
        _emit(
            {
                "error": {
                    "code": code.value,
                    "message": message,
                    "operation": operation,
                    "reason": reason,
                    "retryable": retryable,
                }
            },
            json_output=True,
        )
    else:
        typer.echo(f"observation_{operation}_failed:{reason}: {message}", err=True)
    return exit_code_for(code)


def _bounded_operation(operation: str) -> Callable[[Callable[_P, int]], Callable[_P, int]]:
    """Map the closed pre-store failures of an observe verb to typed public outcomes.

    An empty, missing, or unsafe `--workspace` locator, an unsafe local state
    path, and a bounded storage refusal each name a true operating condition;
    letting them escape to the process catch-all reported all three as
    `internal_error` exit 70 with no next step (#428).
    """

    def decorate(function: Callable[_P, int]) -> Callable[_P, int]:
        @functools.wraps(function)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> int:
            json_output = kwargs.get("json_output", False) is True
            try:
                return function(*args, **kwargs)
            except ValueError as error:
                if str(error) != _WORKSPACE_LOCATOR_INVALID:
                    raise
                return _typed_failure(
                    operation,
                    "workspace_unresolvable",
                    code=PublicErrorCode.INVALID_REQUEST,
                    message=remediation_message("workspace_unresolvable") or "",
                    retryable=False,
                    json_output=json_output,
                )
            except PathSafetyError:
                return _typed_failure(
                    operation,
                    "storage_unsafe",
                    code=PublicErrorCode.STORAGE_UNSAFE,
                    message=remediation_message("storage_unsafe") or "",
                    retryable=False,
                    json_output=json_output,
                )
            except PublicOperationError as error:
                return _typed_failure(
                    operation,
                    error.code.value.lower(),
                    code=error.code,
                    message=error.message,
                    retryable=error.retryable,
                    json_output=json_output,
                )
            except BrokenPipeError:
                # A closed output consumer is not an observation-store failure.
                raise
            except OSError as error:
                if error.errno in _UNSAFE_OBSERVATION_STORAGE_ERRNOS:
                    return _typed_failure(
                        operation,
                        "storage_unsafe",
                        code=PublicErrorCode.STORAGE_UNSAFE,
                        message=remediation_message("storage_unsafe") or "",
                        retryable=False,
                        json_output=json_output,
                    )
                return _typed_failure(
                    operation,
                    "storage_unavailable",
                    code=PublicErrorCode.SERVICE_UNAVAILABLE,
                    message=remediation_message("storage_unavailable") or "",
                    retryable=True,
                    json_output=json_output,
                )

        return wrapped

    return decorate


def _emit(payload: Mapping[str, JsonValue], *, json_output: bool) -> None:
    if json_output:
        typer.echo(canonical_encode(payload).decode("utf-8"))
        return
    for key, value in payload.items():
        typer.echo(f"{key}: {value}")


def _activation_state(
    root: Path,
    *,
    codex_path: Path | None,
    codex_home: Path | None,
) -> str:
    # Activation is a claim about one selected executable and its corresponding home/cache.
    # Never infer that target from ambient process state.
    if codex_path is None or codex_home is None:
        return "unknown"
    try:
        target = IntegrationTarget(IntegrationScope.TRUSTED_PROJECT, os.fspath(root))
        return inspect_activation(
            target,
            executable_path=os.fspath(codex_path),
            codex_home=codex_home,
        ).state.value
    except Exception:
        return "unknown"


@dataclass(frozen=True, slots=True)
class _DeliveryFacts:
    undelivered: int
    pending_causes: dict[str, int]
    last_drain: str
    mapping_present: bool
    # Receipt time of the oldest pending row (its hook received the host event
    # then), or None when nothing is pending. Read with ``last_successful_drain``:
    # a recent oldest row means delivery is keeping pace with the producer; an
    # old one under a fresh drain means the backlog is being worked through
    # from the head; an old one under a stale drain means delivery is stuck and
    # ``pending_delivery_causes`` names why (#564).
    oldest_pending_receipt: str | None


def _delivery_facts(
    store: LocalObservationStore, commitment: str, *, state_root: Path | None
) -> _DeliveryFacts:
    rows = store.list_pending_outbox_rows(commitment)
    causes: dict[str, int] = {}
    for row in rows:
        reason = row.last_reason or "not_attempted"
        causes[reason] = causes.get(reason, 0) + 1
    last_mono = store.last_successful_drain_mono(commitment)
    last = "never" if last_mono is None else f"{max(0.0, time.monotonic() - last_mono):.1f}s ago"
    mapping_present = any(
        load_mapping(session, _state=state_root) is not None
        for session in store.codex_sessions_for_workspace(commitment)
    )
    oldest = min((row.envelope.receipt_time for row in rows), default=None)
    return _DeliveryFacts(
        undelivered=len(rows),
        pending_causes=dict(sorted(causes.items())),
        last_drain=last,
        mapping_present=mapping_present,
        oldest_pending_receipt=None if oldest is None else oldest.wire,
    )


@_bounded_operation("status")
def observe_status(
    *,
    workspace: str | None,
    json_output: bool,
    codex_path: Path | None = None,
    codex_home: Path | None = None,
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
    delivery = _delivery_facts(store, commitment, state_root=_state)
    undelivered = delivery.undelivered
    pending_delivery_causes = delivery.pending_causes
    last_drain = delivery.last_drain
    mapping_present = delivery.mapping_present
    quarantine_depth, quarantine_evicted, quarantine_reclaimed = store.quarantine_facts(commitment)
    # Per-reason depth, so a destroyed-and-replaced-by-gap event (e.g. cursor_stale, #272)
    # is visible as its cause instead of hiding inside one opaque depth number.
    quarantine_causes: dict[str, int] = {}
    for entry in store.list_quarantine(commitment):
        quarantine_causes[entry[2]] = quarantine_causes.get(entry[2], 0) + 1
    quarantine_causes = dict(sorted(quarantine_causes.items()))
    delivery_causes = dict(pending_delivery_causes)
    for reason, count in quarantine_causes.items():
        delivery_causes[reason] = delivery_causes.get(reason, 0) + count
    delivery_causes = dict(sorted(delivery_causes.items()))
    plugin_activation = _activation_state(
        root,
        codex_path=codex_path,
        codex_home=codex_home,
    )
    diagnostics = hook_diagnostic_summary(_state=_state)
    reclaim_guidance = (
        "reclaim with 'yoetz observe reclaim --workspace .'"
        if root == Path.cwd().resolve()
        else (
            "reclaim by changing to the selected workspace and running "
            "'yoetz observe reclaim --workspace .'"
        )
    )
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
                "undelivered_count": undelivered,
                "delivery_causes": delivery_causes,
                "pending_delivery_causes": pending_delivery_causes,
                "oldest_pending_receipt": delivery.oldest_pending_receipt,
                "last_successful_drain": last_drain,
                "quarantine_count": quarantine_depth,
                "quarantine_causes": quarantine_causes,
                "quarantine_evicted_count": quarantine_evicted,
                "quarantine_reclaimed_count": quarantine_reclaimed,
                "mapping_present": mapping_present,
                "hook_diagnostics": diagnostics,
                "plugin_activation": plugin_activation,
            },
            json_output=True,
        )
        return 0
    payload: dict[str, JsonValue] = {
        "workspace_commitment": commitment,
        "consent": consent_label,
        "lifecycle": status.lifecycle.value,
        "lag_events": status.lag_events,
        "undelivered": (
            f"{undelivered} (cause: "
            f"{','.join(f'{key}={value}' for key, value in pending_delivery_causes.items()) or 'none'}; "
            f"oldest: {delivery.oldest_pending_receipt or 'none'}; "
            f"last successful drain: {last_drain})"
        ),
        "quarantine": (
            f"{quarantine_depth} (cause: "
            f"{','.join(f'{key}={value}' for key, value in quarantine_causes.items()) or 'none'}; "
            f"evicted: {quarantine_evicted}; "
            f"reclaimed: {quarantine_reclaimed}; "
            f"{reclaim_guidance})"
            if quarantine_depth or quarantine_evicted or quarantine_reclaimed
            else "0"
        ),
        "mapping_present": str(mapping_present),
        "plugin_activation": plugin_activation,
        "hook_diagnostics": canonical_encode(diagnostics).decode("utf-8"),
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
    if plugin_activation == "installed_not_activated":
        typer.echo("plugin installed but not activated in Codex — run 'yoetz setup'")
    return 0


_DRAIN_TERMINAL_DRAINED: Final = "drained"
_DRAIN_TERMINAL_RETRY_PENDING: Final = "retry_pending"
_DRAIN_TERMINAL_SERVICE_UNAVAILABLE: Final = "service_unavailable"
_DRAIN_TERMINAL_PASS_LIMIT: Final = "pass_limit"


@dataclass(slots=True)
class _DrainTally:
    attempted: int = 0
    acknowledged: int = 0
    retry_pending: int = 0
    quarantined: int = 0
    reasons: dict[str, int] = field(default_factory=dict[str, int])

    def note_reason(self, reason: str, count: int = 1) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + count

    def summary(self, *, passes: int, pending_after: int, terminal: str) -> JsonObject:
        return JsonObject(
            {
                "attempted": self.attempted,
                "acknowledged": self.acknowledged,
                "retry_pending": self.retry_pending,
                "quarantined": self.quarantined,
                "reasons": JsonObject(dict(sorted(self.reasons.items()))),
                "passes": passes,
                "pending_after": pending_after,
                "terminal": terminal,
            }
        )


def _collect_deliverable(
    store: LocalObservationStore, workspaces: tuple[str, ...], tally: _DrainTally
) -> list[tuple[str, ObservationOutboxRow]]:
    """Snapshot every pending row, quarantining setup-probe rows on the way."""

    deliverable: list[tuple[str, ObservationOutboxRow]] = []
    for commitment in workspaces:
        for row in store.list_pending_outbox_rows(commitment):
            if row.codex_session_id == _SETUP_PROBE_SESSION:
                if store.quarantine_outbox_row(commitment, row, "setup_probe"):
                    tally.quarantined += 1
                    tally.note_reason("setup_probe")
                continue
            deliverable.append((commitment, row))
    return deliverable


async def _drain_pass(
    store: LocalObservationStore,
    client: _DrainClient,
    deliverable: list[tuple[str, ObservationOutboxRow]],
    tally: _DrainTally,
) -> int:
    """Attempt one FIFO pass over the snapshot; return the rows it resolved.

    A retryable rejection retires its lane for the pass (delivering a later
    row of the same session would advance the ingest cursor past the failed
    head, #272), and a workspace-global reason stops that workspace.
    """

    resolved = 0
    retired_lanes: set[tuple[str, str]] = set()
    stopped_workspaces: set[str] = set()
    for commitment, row in deliverable:
        lane = (commitment, row.codex_session_id)
        if commitment in stopped_workspaces or lane in retired_lanes:
            continue
        tally.attempted += 1
        try:
            raw = await client.observation_ingest(
                observation_ingest_request_to_json(
                    ObservationIngestRequest(
                        codex_session_id=row.codex_session_id,
                        envelope=row.envelope,
                    )
                ),
                deadline_ms=_DRAIN_DEADLINE_MS,
            )
            result = observation_ingest_result_from_json(raw)
        except ControlError as exc:
            reason = (
                ObservationGapCode.VAULT_LOCKED.value
                if exc.reason == "vault_locked"
                else (
                    ObservationGapCode.SERVICE_UNAVAILABLE.value
                    if exc.retryable
                    else ObservationGapCode.LEDGER_REJECTED.value
                )
            )
            result = ObservationIngestResult(ObservationIngestDisposition.REJECTED, reason, None)
        except Exception:
            result = ObservationIngestResult(
                ObservationIngestDisposition.REJECTED,
                ObservationGapCode.SERVICE_UNAVAILABLE.value,
                None,
            )
        decision = route_observation_ingest(result, row=row)
        updated = store.bump_outbox_row_attempt(commitment, row, reason=decision.reason)
        if updated is None:
            retired_lanes.add(lane)
            continue
        if decision.reason is not None:
            tally.note_reason(decision.reason)
            if decision.reason not in EXPECTED_OBSERVATION_BACKPRESSURE_REASONS:
                store.note_coverage_gap(commitment, decision.reason)
        if decision.action is ObservationDrainAction.RETRY:
            retired_lanes.add(lane)
            if decision.reason == ObservationGapCode.MAPPING_MISSING.value:
                # Terminalization takes the session's lifecycle lock so an
                # attach still persisting its mapping wins (#275). A lock or
                # store failure is this lane's problem only: the rows stay
                # pending for the next run and the other lanes keep draining,
                # as in the hook drain (#554).
                moved = 0
                with contextlib.suppress(Exception):
                    moved = store.quarantine_ended_unmapped_session(
                        commitment,
                        row.codex_session_id,
                        decision.reason,
                    )
                if moved:
                    tally.quarantined += moved
                    resolved += moved
                    continue
            tally.retry_pending += 1
            if decision.reason is not None:
                # Stamp the retired siblings with the shared cause so
                # `observe status` never reports them as not_attempted.
                with contextlib.suppress(Exception):
                    store.note_outbox_session_reason(
                        commitment,
                        row.codex_session_id,
                        decision.reason,
                    )
            if decision.reason in WORKSPACE_GLOBAL_OBSERVATION_STOP_REASONS:
                stopped_workspaces.add(commitment)
        elif decision.action is ObservationDrainAction.QUARANTINE:
            if store.quarantine_outbox_row(
                commitment,
                updated,
                decision.reason or ObservationGapCode.SERVICE_UNAVAILABLE.value,
            ):
                tally.quarantined += 1
                resolved += 1
        elif store.acknowledge_outbox_row(commitment, updated):
            tally.acknowledged += 1
            resolved += 1
    return resolved


async def _drain_observation_async(
    *,
    workspace: str | None,
    _state: Path | None,
    connect: DrainConnector = cast(DrainConnector, connect_service_on_demand),
) -> tuple[int, JsonObject]:
    """Drain to a documented terminal condition without sleeping (#564).

    One pass retires a lane at its first retryable head, so a single pass over
    a busy backlog cleared only the rows ahead of the first back-pressure
    answer and stopped. Passes now repeat while the previous one resolved at
    least one row and rows remain; the pass count is bounded by the backlog
    size at entry, so a still-running producer cannot keep this loop alive.
    ``terminal`` names why the loop ended:

    - ``drained``: no pending row remains in the selected workspaces;
    - ``retry_pending``: a full pass resolved nothing; every remaining lane
      head is retryable and ``reasons`` names why (a check barrier's
      ``operation_pending`` clears when the check completes, ``mapping_missing``
      when the session maps or ends, ``paused``/``vault_locked`` when lifted);
    - ``service_unavailable``: no connection could be opened (exit 20);
    - ``pass_limit``: passes kept resolving rows until the bound, so a producer
      is still adding rows faster than they were listed; run again once it
      stops.
    """

    store = LocalObservationStore(_state=_state)
    if workspace is None:
        workspaces = store.pending_workspaces()
    else:
        workspaces = (store.workspace_commitment(str(_resolve_workspace(workspace))),)
    tally = _DrainTally()
    # READY and the explicit drain command both converge lifecycle intents;
    # SessionStart has no deliverable outbox row of its own.
    for commitment in workspaces:
        with contextlib.suppress(Exception):
            store.reconcile_pending_session_lifecycles(commitment)
    passes = 0
    terminal = _DRAIN_TERMINAL_DRAINED
    client: _DrainClient | None = None
    try:
        deliverable = _collect_deliverable(store, workspaces, tally)
        pass_limit = max(1, len(deliverable))
        while deliverable:
            if client is None:
                try:
                    client = await connect(ControlClientKind.CLI)
                except Exception:
                    tally.note_reason(
                        ObservationGapCode.SERVICE_UNAVAILABLE.value, len(deliverable)
                    )
                    tally.retry_pending += len(deliverable)
                    return 20, tally.summary(
                        passes=passes,
                        pending_after=len(deliverable),
                        terminal=_DRAIN_TERMINAL_SERVICE_UNAVAILABLE,
                    )
            passes += 1
            resolved = await _drain_pass(store, client, deliverable, tally)
            if resolved == 0:
                terminal = _DRAIN_TERMINAL_RETRY_PENDING
                break
            if passes >= pass_limit:
                deliverable = _collect_deliverable(store, workspaces, tally)
                if deliverable:
                    terminal = _DRAIN_TERMINAL_PASS_LIMIT
                break
            deliverable = _collect_deliverable(store, workspaces, tally)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.close()
    pending_after = sum(store.pending_outbox_count(commitment) for commitment in workspaces)
    if pending_after == 0:
        terminal = _DRAIN_TERMINAL_DRAINED
    return 0, tally.summary(passes=passes, pending_after=pending_after, terminal=terminal)


@_bounded_operation("drain")
def drain_observation(
    *, workspace: str | None, json_output: bool, _state: Path | None = None
) -> int:
    """User-invoked delivery pass; starts the service when necessary."""

    import anyio

    async def run() -> tuple[int, JsonObject]:
        return await _drain_observation_async(workspace=workspace, _state=_state)

    code, summary = anyio.run(run)
    if json_output:
        _emit(summary, json_output=True)
    else:
        typer.echo(
            "observation drain: "
            f"attempted={summary['attempted']} acknowledged={summary['acknowledged']} "
            f"retry_pending={summary['retry_pending']} quarantined={summary['quarantined']} "
            f"passes={summary['passes']} pending_after={summary['pending_after']} "
            f"terminal={summary['terminal']}"
        )
        reasons = summary["reasons"]
        assert isinstance(reasons, Mapping)
        typer.echo(
            "reasons: " + (", ".join(f"{key}={value}" for key, value in reasons.items()) or "none")
        )
    return code


@_bounded_operation("reclaim")
def reclaim_observation(*, workspace: str, json_output: bool, _state: Path | None = None) -> int:
    """Operator-initiated drop of quarantined observation detail (#211).

    Quarantine detail is a diagnostic aid whose only ongoing effect is
    per-hook parse/encode tax; once the underlying delivery failure is fixed,
    this is how a recovered install sheds it. The drop extends the aggregate
    eviction commitment chain and is counted separately from involuntary
    evictions, never silent and never conflated with data loss.
    """

    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(_resolve_workspace(workspace)))
    reclaimed = store.reclaim_quarantine(commitment)
    depth, evicted, total_reclaimed = store.quarantine_facts(commitment)
    if json_output:
        _emit(
            {
                "workspace_commitment": commitment,
                "reclaimed": reclaimed,
                "quarantine_count": depth,
                "quarantine_evicted_count": evicted,
                "quarantine_reclaimed_count": total_reclaimed,
            },
            json_output=True,
        )
        return 0
    typer.echo(f"observation_quarantine_reclaimed:{reclaimed} (reclaimed total: {total_reclaimed})")
    return 0


@_bounded_operation("grant")
def grant_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    root = _resolve_workspace(workspace)
    commitment = store.workspace_commitment(str(root))
    store.grant_consent(commitment)
    # Never log the raw path — only the commitment.
    typer.echo(f"observation_consent_granted:{commitment}")
    return 0


@_bounded_operation("pause")
def pause_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(_resolve_workspace(workspace)))
    status = store.pause(ObservationControlCommand(commitment))
    typer.echo(f"observation_paused:{status.lifecycle.value}")
    return 0


@_bounded_operation("resume")
def resume_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(_resolve_workspace(workspace)))
    status = store.resume(ObservationControlCommand(commitment))
    typer.echo(f"observation_resumed:{status.lifecycle.value}")
    return 0


@_bounded_operation("revoke")
def revoke_observation(*, workspace: str, _state: Path | None = None) -> int:
    store = LocalObservationStore(_state=_state)
    commitment = store.workspace_commitment(str(_resolve_workspace(workspace)))
    status = store.revoke(ObservationRevokeCommand(commitment, retain_evidence=True))
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


@_bounded_operation("reconcile")
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
    # Explicit recovery shares cursor/profile, source identity, and pairing persistence with
    # locator-driven reconciliation, including blocked delivery and mapping upgrades.
    payload = reconcile_session_stream_path(
        store,
        workspace_commitment=workspace_commitment,
        session_commitment=session_commitment,
        codex_session_id=session_token[:128],
        path=path,
    )
    payload = {**payload, "mode": "recovery_explicit_path"}
    _emit(payload, json_output=json_output)
    return 0
