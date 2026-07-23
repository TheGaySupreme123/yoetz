"""In-memory reference ObservationPort for consent, cursors, and structural envelopes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from yoetz.domain.observation import (
    ObservationControlCommand,
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatus,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

__all__ = [
    "MemoryObservationConsent",
    "MemoryObservationState",
    "MemoryObservationStore",
]


def _error(code: PublicErrorCode, message: str, *, retryable: bool) -> PublicOperationError:
    return PublicOperationError(code, message, retryable)


@dataclass(frozen=True, slots=True)
class MemoryObservationConsent:
    workspace_commitment: str
    granted_at: Timestamp
    revoked_at: Timestamp | None = None
    paused: bool = False

    @property
    def active(self) -> bool:
        return self.revoked_at is None and not self.paused


@dataclass
class MemoryObservationState:
    consent: dict[str, MemoryObservationConsent] = field(
        default_factory=dict[str, MemoryObservationConsent]
    )
    session_workspaces: dict[str, str] = field(default_factory=dict[str, str])
    cursors: dict[tuple[str, ObservationSource, str], ObservationCursor] = field(
        default_factory=dict[tuple[str, ObservationSource, str], ObservationCursor]
    )
    dedup: set[str] = field(default_factory=set[str])
    envelopes: list[tuple[str, ObservationEnvelope]] = field(
        default_factory=list[tuple[str, ObservationEnvelope]]
    )
    advice_frontier: dict[str, str] = field(default_factory=dict[str, str])
    unsupported_events: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    gaps: dict[str, set[str]] = field(default_factory=dict[str, set[str]])
    last_receipt: dict[str, Timestamp] = field(default_factory=dict[str, Timestamp])


def _cursor_payload(cursor: ObservationCursor) -> JsonObject:
    return JsonObject(
        {
            "source_generation": cursor.source_generation,
            "byte_position": cursor.byte_position,
            "event_position": cursor.event_position,
            "last_source_commitment": cursor.last_source_commitment,
            "mapping_version": cursor.mapping_version,
        }
    )


def _dedup_key(workspace: str, envelope: ObservationEnvelope) -> str:
    return canonical_digest(
        JsonObject(
            {
                "workspace_commitment": workspace,
                "session_commitment": envelope.session_commitment,
                "source": envelope.source.value,
                "source_identity": envelope.source_identity,
                "event_kind": envelope.event_kind,
                "cursor": _cursor_payload(envelope.cursor),
            }
        )
    )


class MemoryObservationStore:
    """Reference ObservationPort with fail-closed consent and generation-fenced cursors."""

    def __init__(self, state: MemoryObservationState | None = None) -> None:
        self._state = state if state is not None else MemoryObservationState()
        self._lock = asyncio.Lock()

    def grant_consent(self, workspace_commitment: str, granted_at: Timestamp) -> None:
        self._state.consent[workspace_commitment] = MemoryObservationConsent(
            workspace_commitment=workspace_commitment,
            granted_at=granted_at,
            revoked_at=None,
            paused=False,
        )

    def bind_session(self, workspace_commitment: str, session_commitment: str) -> None:
        if workspace_commitment not in self._state.consent:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation consent is missing.",
                retryable=False,
            )
        existing = self._state.session_workspaces.get(session_commitment)
        if existing is not None and existing != workspace_commitment:
            raise _error(
                PublicErrorCode.SESSION_CONFLICT,
                "Observation session is already bound.",
                retryable=False,
            )
        self._state.session_workspaces[session_commitment] = workspace_commitment

    async def ingest(self, envelope: ObservationEnvelope) -> ObservationIngestResult:
        if type(envelope) is not ObservationEnvelope:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation envelope is invalid.",
                retryable=False,
            )
        async with self._lock:
            try:
                workspace = self._workspace_for_envelope(envelope)
            except PublicOperationError:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_MISSING.value,
                    None,
                )
            consent = self._state.consent.get(workspace)
            if consent is None:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_MISSING.value,
                    None,
                )
            if consent.revoked_at is not None:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_REVOKED.value,
                    None,
                )
            if consent.paused:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    "paused",
                    None,
                )
            key = _dedup_key(workspace, envelope)
            if key in self._state.dedup:
                cursor = self._state.cursors.get(
                    (workspace, envelope.source, envelope.session_commitment)
                )
                return ObservationIngestResult(
                    ObservationIngestDisposition.DUPLICATE,
                    "duplicate",
                    cursor,
                )
            cursor_key = (workspace, envelope.source, envelope.session_commitment)
            existing = self._state.cursors.get(cursor_key)
            if existing is not None and envelope.cursor.is_stale_relative_to(existing):
                self._note_gap(workspace, ObservationGapCode.CURSOR_STALE.value)
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CURSOR_STALE.value,
                    existing,
                )
            if (
                existing is not None
                and envelope.cursor.source_generation < existing.source_generation
            ):
                self._note_gap(workspace, ObservationGapCode.CURSOR_STALE.value)
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CURSOR_STALE.value,
                    existing,
                )
            self._state.dedup.add(key)
            self._state.cursors[cursor_key] = envelope.cursor
            self._state.envelopes.append((workspace, envelope))
            self._state.last_receipt[workspace] = envelope.receipt_time
            for gap in envelope.gap_codes:
                self._note_gap(workspace, gap)
            if ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes:
                self._state.unsupported_events.setdefault(workspace, set()).add(envelope.event_kind)
            # Force encode to keep structural bytes bounded at rest.
            _ = canonical_encode(
                JsonObject(
                    {
                        "structural_payload": envelope.structural_payload,
                        "content_object_refs": envelope.content_object_refs,
                    }
                )
            )
            return ObservationIngestResult(
                ObservationIngestDisposition.ACCEPTED,
                None,
                envelope.cursor,
            )

    async def status(self, query: ObservationStatusQuery) -> ObservationStatus:
        if type(query) is not ObservationStatusQuery:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation status query is invalid.",
                retryable=False,
            )
        async with self._lock:
            return self._status_unlocked(query.workspace_commitment)

    async def pause(self, command: ObservationControlCommand) -> ObservationStatus:
        if type(command) is not ObservationControlCommand:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation control command is invalid.",
                retryable=False,
            )
        async with self._lock:
            consent = self._require_consent(command.workspace_commitment)
            if consent.revoked_at is not None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is revoked.",
                    retryable=False,
                )
            self._state.consent[command.workspace_commitment] = MemoryObservationConsent(
                workspace_commitment=consent.workspace_commitment,
                granted_at=consent.granted_at,
                revoked_at=consent.revoked_at,
                paused=True,
            )
            return self._status_unlocked(command.workspace_commitment)

    async def resume(self, command: ObservationControlCommand) -> ObservationStatus:
        if type(command) is not ObservationControlCommand:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation control command is invalid.",
                retryable=False,
            )
        async with self._lock:
            consent = self._state.consent.get(command.workspace_commitment)
            if consent is None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is missing.",
                    retryable=False,
                )
            if consent.revoked_at is not None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is revoked.",
                    retryable=False,
                )
            self._state.consent[command.workspace_commitment] = MemoryObservationConsent(
                workspace_commitment=consent.workspace_commitment,
                granted_at=consent.granted_at,
                revoked_at=None,
                paused=False,
            )
            return self._status_unlocked(command.workspace_commitment)

    async def revoke(self, command: ObservationRevokeCommand) -> ObservationStatus:
        if type(command) is not ObservationRevokeCommand:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation revoke command is invalid.",
                retryable=False,
            )
        async with self._lock:
            consent = self._require_consent(command.workspace_commitment)
            revoked_at = self._state.last_receipt.get(
                command.workspace_commitment, consent.granted_at
            )
            self._state.consent[command.workspace_commitment] = MemoryObservationConsent(
                workspace_commitment=consent.workspace_commitment,
                granted_at=consent.granted_at,
                revoked_at=revoked_at,
                paused=True,
            )
            # Evidence retained: envelopes/dedup/cursors remain.
            return self._status_unlocked(command.workspace_commitment)

    def _workspace_for_envelope(self, envelope: ObservationEnvelope) -> str:
        bound = self._state.session_workspaces.get(envelope.session_commitment)
        if bound is not None:
            return bound
        if len(self._state.consent) == 1:
            workspace = next(iter(self._state.consent))
            self._state.session_workspaces[envelope.session_commitment] = workspace
            return workspace
        raise _error(
            PublicErrorCode.INVALID_REQUEST,
            "Observation workspace consent is missing.",
            retryable=False,
        )

    def _require_consent(self, workspace_commitment: str) -> MemoryObservationConsent:
        consent = self._state.consent.get(workspace_commitment)
        if consent is None:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation consent is missing.",
                retryable=False,
            )
        return consent

    def _note_gap(self, workspace: str, code: str) -> None:
        self._state.gaps.setdefault(workspace, set()).add(code)

    def _status_unlocked(self, workspace_commitment: str) -> ObservationStatus:
        import time

        from yoetz.application.observation_health import (
            DEFAULT_OBSERVATION_HEALTH_THRESHOLDS,
            ObservationHealthSignals,
            compute_observation_lifecycle,
        )

        consent = self._state.consent.get(workspace_commitment)
        coverage = {
            ObservationSource.CODEX_HOOK: False,
            ObservationSource.CODEX_SESSION_STREAM: False,
        }
        for workspace, envelope in self._state.envelopes:
            if workspace == workspace_commitment:
                coverage[envelope.source] = True
        gaps = tuple(sorted(self._state.gaps.get(workspace_commitment, set()), key=str.encode))
        unsupported = tuple(
            sorted(self._state.unsupported_events.get(workspace_commitment, set()), key=str.encode)
        )
        consent_active = consent is not None and consent.revoked_at is None and not consent.paused
        last_receipt = self._state.last_receipt.get(workspace_commitment)
        progress = time.monotonic() if last_receipt is not None and any(coverage.values()) else None
        signals = ObservationHealthSignals(
            consent_active=consent_active,
            mapping_available=workspace_commitment in self._state.session_workspaces.values()
            or workspace_commitment in self._state.consent,
            source_coverage=coverage,
            pending_outbox_count=0,
            lag_events=0,
            gaps=gaps,
            unsupported_events=unsupported,
            advice_frontier=self._state.advice_frontier.get(workspace_commitment),
            last_hook_receipt_monotonic=progress,
            last_stream_advancement_monotonic=None,
            last_successful_drain_monotonic=progress,
            session_ended=False,
        )
        lifecycle = compute_observation_lifecycle(
            signals,
            now_monotonic=time.monotonic(),
            thresholds=DEFAULT_OBSERVATION_HEALTH_THRESHOLDS,
        )
        return ObservationStatus(
            lifecycle=lifecycle,
            workspace_commitment=workspace_commitment,
            source_coverage=coverage,
            last_observation_receipt_time=last_receipt,
            lag_events=0,
            gaps=gaps,
            unsupported_events=unsupported,
            advice_frontier=self._state.advice_frontier.get(workspace_commitment),
        )
