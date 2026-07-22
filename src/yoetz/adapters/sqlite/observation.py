"""SQLite ObservationPort over bundle migration 0002 observation tables."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Final, cast

import apsw

from yoetz.domain.observation import (
    AdviceSnapshot,
    ObservationControlCommand,
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationLifecycle,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatus,
    ObservationStatusQuery,
    advice_snapshot_from_json,
    advice_snapshot_to_json,
    observation_cursor_to_json,
    observation_envelope_from_json,
    observation_envelope_to_json,
)
from yoetz.domain.values import JsonObject, JsonValue, Timestamp
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError

__all__ = ["SqliteObservationStore"]

_MAX_EVENTS: Final = 256
_MAX_DEDUP: Final = 4_096


def _error(code: PublicErrorCode, message: str, *, retryable: bool) -> PublicOperationError:
    return PublicOperationError(code, message, retryable)


def _dedup_key(workspace: str, envelope: ObservationEnvelope) -> str:
    return canonical_digest(
        JsonObject(
            {
                "workspace_commitment": workspace,
                "session_commitment": envelope.session_commitment,
                "source": envelope.source.value,
                "source_identity": envelope.source_identity,
                "event_kind": envelope.event_kind,
                "cursor": observation_cursor_to_json(envelope.cursor),
            }
        )
    )


class SqliteObservationStore:
    """Durable ObservationPort backed by migration ``0002`` observation tables."""

    def __init__(self, connection: apsw.Connection) -> None:
        self._db = connection
        self._lock = asyncio.Lock()
        # Session → workspace binding kept in-process; durable sessions appear via cursors/events.
        self._session_workspaces: dict[str, str] = {}

    def grant_consent(self, workspace_commitment: str, granted_at: Timestamp) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO observation_consent(workspace_commitment, granted_at, revoked_at, paused) "
                "VALUES (?, ?, NULL, 0) "
                "ON CONFLICT(workspace_commitment) DO UPDATE SET "
                "granted_at=excluded.granted_at, revoked_at=NULL, paused=0",
                (workspace_commitment, granted_at.wire),
            )

    def bind_session(self, workspace_commitment: str, session_commitment: str) -> None:
        consent = self._consent_row(workspace_commitment)
        if consent is None:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation consent is missing.",
                retryable=False,
            )
        existing = self._session_workspaces.get(session_commitment)
        if existing is not None and existing != workspace_commitment:
            raise _error(
                PublicErrorCode.SESSION_CONFLICT,
                "Observation session is already bound.",
                retryable=False,
            )
        self._session_workspaces[session_commitment] = workspace_commitment

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
            consent = self._consent_row(workspace)
            if consent is None:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_MISSING.value,
                    None,
                )
            revoked_at, paused, _granted = consent
            if revoked_at is not None:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_REVOKED.value,
                    None,
                )
            if paused:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    "paused",
                    None,
                )
            key = _dedup_key(workspace, envelope)
            existing_dedup = self._db.execute(
                "SELECT 1 FROM observation_dedup WHERE dedup_key = ?", (key,)
            ).fetchone()
            if existing_dedup is not None:
                cursor = self._load_cursor(workspace, envelope.source, envelope.session_commitment)
                return ObservationIngestResult(
                    ObservationIngestDisposition.DUPLICATE,
                    "duplicate",
                    cursor,
                )
            existing = self._load_cursor(workspace, envelope.source, envelope.session_commitment)
            if existing is not None and envelope.cursor.is_stale_relative_to(existing):
                self._note_gap_event(workspace, envelope, ObservationGapCode.CURSOR_STALE.value)
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CURSOR_STALE.value,
                    existing,
                )
            if (
                existing is not None
                and envelope.cursor.source_generation < existing.source_generation
            ):
                self._note_gap_event(workspace, envelope, ObservationGapCode.CURSOR_STALE.value)
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CURSOR_STALE.value,
                    existing,
                )
            with self._db:
                self._db.execute(
                    "INSERT INTO observation_dedup(dedup_key, workspace_commitment, ingested_at) "
                    "VALUES (?, ?, ?)",
                    (key, workspace, envelope.receipt_time.wire),
                )
                self._upsert_cursor(workspace, envelope)
                self._insert_event(workspace, envelope)
                self._trim_retention(workspace)
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
            if consent[0] is not None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is revoked.",
                    retryable=False,
                )
            with self._db:
                self._db.execute(
                    "UPDATE observation_consent SET paused = 1 WHERE workspace_commitment = ?",
                    (command.workspace_commitment,),
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
            consent = self._require_consent(command.workspace_commitment)
            if consent[0] is not None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is revoked.",
                    retryable=False,
                )
            with self._db:
                self._db.execute(
                    "UPDATE observation_consent SET paused = 0, revoked_at = NULL "
                    "WHERE workspace_commitment = ?",
                    (command.workspace_commitment,),
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
            revoked_at = self._last_receipt(command.workspace_commitment)
            stamp = revoked_at if revoked_at is not None else consent[2]
            with self._db:
                self._db.execute(
                    "UPDATE observation_consent SET revoked_at = ?, paused = 1 "
                    "WHERE workspace_commitment = ?",
                    (stamp, command.workspace_commitment),
                )
            return self._status_unlocked(command.workspace_commitment)

    def set_advice_snapshot(
        self, workspace: str, snapshot: AdviceSnapshot, updated_at: Timestamp
    ) -> None:
        payload = canonical_encode(advice_snapshot_to_json(snapshot))
        with self._db:
            self._db.execute(
                "INSERT INTO observation_advice(workspace_commitment, suppression_identity, "
                "snapshot_json, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace_commitment) DO UPDATE SET "
                "suppression_identity=excluded.suppression_identity, "
                "snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at",
                (workspace, snapshot.suppression_identity, payload, updated_at.wire),
            )

    def load_advice_snapshot(self, workspace: str) -> AdviceSnapshot | None:
        row = self._db.execute(
            "SELECT snapshot_json FROM observation_advice WHERE workspace_commitment = ?",
            (workspace,),
        ).fetchone()
        if row is None or type(row[0]) is not bytes:
            return None
        parsed = strict_json_parse(row[0])
        if not isinstance(parsed, Mapping):
            return None
        return advice_snapshot_from_json(JsonObject(cast(Mapping[str, JsonValue], parsed)))

    def _consent_row(self, workspace: str) -> tuple[str | None, bool, str] | None:
        row = self._db.execute(
            "SELECT revoked_at, paused, granted_at FROM observation_consent "
            "WHERE workspace_commitment = ?",
            (workspace,),
        ).fetchone()
        if row is None:
            return None
        revoked_at = row[0] if type(row[0]) is str else None
        paused = bool(row[1])
        granted_at = cast(str, row[2])
        return revoked_at, paused, granted_at

    def _require_consent(self, workspace: str) -> tuple[str | None, bool, str]:
        consent = self._consent_row(workspace)
        if consent is None:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation consent is missing.",
                retryable=False,
            )
        return consent

    def _workspace_for_envelope(self, envelope: ObservationEnvelope) -> str:
        bound = self._session_workspaces.get(envelope.session_commitment)
        if bound is not None:
            return bound
        row = self._db.execute(
            "SELECT workspace_commitment FROM observation_cursors WHERE session_commitment = ? "
            "LIMIT 1",
            (envelope.session_commitment,),
        ).fetchone()
        if row is not None and type(row[0]) is str:
            self._session_workspaces[envelope.session_commitment] = row[0]
            return row[0]
        active = self._db.execute(
            "SELECT workspace_commitment FROM observation_consent "
            "WHERE revoked_at IS NULL AND paused = 0"
        ).fetchall()
        if len(active) == 1 and type(active[0][0]) is str:
            workspace = active[0][0]
            self._session_workspaces[envelope.session_commitment] = workspace
            return workspace
        raise _error(
            PublicErrorCode.INVALID_REQUEST,
            "Observation workspace consent is missing.",
            retryable=False,
        )

    def _load_cursor(
        self, workspace: str, source: ObservationSource, session: str
    ) -> ObservationCursor | None:
        row = self._db.execute(
            "SELECT generation, byte_pos, event_pos, last_source_commitment, mapping_version "
            "FROM observation_cursors WHERE workspace_commitment = ? AND source = ? "
            "AND session_commitment = ?",
            (workspace, source.value, session),
        ).fetchone()
        if row is None:
            return None
        return ObservationCursor(
            source_generation=cast(int, row[0]),
            byte_position=cast(int, row[1]),
            event_position=cast(int, row[2]),
            last_source_commitment=cast(str, row[3]),
            mapping_version=cast(str, row[4]),
        )

    def _upsert_cursor(self, workspace: str, envelope: ObservationEnvelope) -> None:
        cursor = envelope.cursor
        self._db.execute(
            "INSERT INTO observation_cursors(workspace_commitment, source, session_commitment, "
            "generation, byte_pos, event_pos, last_source_commitment, mapping_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace_commitment, source, session_commitment) DO UPDATE SET "
            "generation=excluded.generation, byte_pos=excluded.byte_pos, "
            "event_pos=excluded.event_pos, last_source_commitment=excluded.last_source_commitment, "
            "mapping_version=excluded.mapping_version",
            (
                workspace,
                envelope.source.value,
                envelope.session_commitment,
                cursor.source_generation,
                cursor.byte_position,
                cursor.event_position,
                cursor.last_source_commitment,
                cursor.mapping_version,
            ),
        )

    def _insert_event(self, workspace: str, envelope: ObservationEnvelope) -> None:
        wire = observation_envelope_to_json(envelope)
        # Full envelope wire in structural_json for lossless reload; refs/gaps denormalized.
        structural = canonical_encode(wire)
        refs = canonical_encode(wire["content_object_refs"])
        gaps = canonical_encode(wire["gap_codes"])
        self._db.execute(
            "INSERT INTO observation_events("
            "workspace_commitment, session_commitment, source, event_kind, structural_json, "
            "content_refs_json, gap_codes_json, receipt_time, source_generation, byte_position, "
            "event_position, last_source_commitment, mapping_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workspace,
                envelope.session_commitment,
                envelope.source.value,
                envelope.event_kind,
                structural,
                refs,
                gaps,
                envelope.receipt_time.wire,
                envelope.cursor.source_generation,
                envelope.cursor.byte_position,
                envelope.cursor.event_position,
                envelope.cursor.last_source_commitment,
                envelope.cursor.mapping_version,
            ),
        )

    def _note_gap_event(self, workspace: str, envelope: ObservationEnvelope, gap_code: str) -> None:
        """Persist a gap without advancing the primary source cursor."""

        gap_envelope = ObservationEnvelope(
            session_commitment=envelope.session_commitment,
            event_kind="observation_gap",
            source_identity=f"gap:{gap_code}:{envelope.source_identity[-24:]}",
            source=envelope.source,
            cursor=envelope.cursor,
            receipt_time=envelope.receipt_time,
            structural_payload=JsonObject({"hook_name": "observation_gap"}),
            content_object_refs=(),
            gap_codes=(gap_code,),
        )
        key = _dedup_key(workspace, gap_envelope)
        with self._db:
            existing = self._db.execute(
                "SELECT 1 FROM observation_dedup WHERE dedup_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                return
            self._db.execute(
                "INSERT INTO observation_dedup(dedup_key, workspace_commitment, ingested_at) "
                "VALUES (?, ?, ?)",
                (key, workspace, gap_envelope.receipt_time.wire),
            )
            self._insert_event(workspace, gap_envelope)

    def _trim_retention(self, workspace: str) -> None:
        count_row = self._db.execute(
            "SELECT COUNT(*) FROM observation_events WHERE workspace_commitment = ?",
            (workspace,),
        ).fetchone()
        count = cast(int, count_row[0]) if count_row is not None else 0
        if count > _MAX_EVENTS:
            excess = count - _MAX_EVENTS
            self._db.execute(
                "DELETE FROM observation_events WHERE id IN ("
                "SELECT id FROM observation_events WHERE workspace_commitment = ? "
                "ORDER BY id ASC LIMIT ?)",
                (workspace, excess),
            )
        dedup_row = self._db.execute(
            "SELECT COUNT(*) FROM observation_dedup WHERE workspace_commitment = ?",
            (workspace,),
        ).fetchone()
        dedup_count = cast(int, dedup_row[0]) if dedup_row is not None else 0
        if dedup_count > _MAX_DEDUP:
            excess = dedup_count - _MAX_DEDUP
            self._db.execute(
                "DELETE FROM observation_dedup WHERE dedup_key IN ("
                "SELECT dedup_key FROM observation_dedup WHERE workspace_commitment = ? "
                "ORDER BY ingested_at ASC LIMIT ?)",
                (workspace, excess),
            )

    def _last_receipt(self, workspace: str) -> str | None:
        row = self._db.execute(
            "SELECT receipt_time FROM observation_events WHERE workspace_commitment = ? "
            "ORDER BY id DESC LIMIT 1",
            (workspace,),
        ).fetchone()
        return cast(str, row[0]) if row is not None and type(row[0]) is str else None

    def _status_unlocked(self, workspace_commitment: str) -> ObservationStatus:
        consent = self._consent_row(workspace_commitment)
        coverage = {
            ObservationSource.CODEX_HOOK: False,
            ObservationSource.CODEX_SESSION_STREAM: False,
        }
        gaps: set[str] = set()
        unsupported: set[str] = set()
        rows = self._db.execute(
            "SELECT source, event_kind, gap_codes_json FROM observation_events "
            "WHERE workspace_commitment = ?",
            (workspace_commitment,),
        ).fetchall()
        for row in rows:
            source = ObservationSource(cast(str, row[0]))
            coverage[source] = True
            event_kind = cast(str, row[1])
            gap_blob = row[2]
            if type(gap_blob) is bytes:
                parsed = strict_json_parse(gap_blob)
                if type(parsed) is list:
                    for item in cast(list[object], parsed):
                        if type(item) is str:
                            gaps.add(item)
                            if item == ObservationGapCode.UNSUPPORTED_EVENT.value:
                                unsupported.add(event_kind)
        if consent is None:
            lifecycle = ObservationLifecycle.STOPPED
        elif consent[0] is not None:
            lifecycle = ObservationLifecycle.STOPPED
        elif consent[1]:
            lifecycle = ObservationLifecycle.STOPPED
        elif not any(coverage.values()):
            lifecycle = ObservationLifecycle.DEGRADED
        else:
            lifecycle = ObservationLifecycle.ACTIVE
        advice_row = self._db.execute(
            "SELECT suppression_identity FROM observation_advice WHERE workspace_commitment = ?",
            (workspace_commitment,),
        ).fetchone()
        advice_frontier = (
            cast(str, advice_row[0])
            if advice_row is not None and type(advice_row[0]) is str
            else None
        )
        last = self._last_receipt(workspace_commitment)
        return ObservationStatus(
            lifecycle=lifecycle,
            workspace_commitment=workspace_commitment,
            source_coverage=coverage,
            last_observation_receipt_time=(None if last is None else Timestamp(last)),
            lag_events=0,
            gaps=tuple(sorted(gaps, key=str.encode)),
            unsupported_events=tuple(sorted(unsupported, key=str.encode)),
            advice_frontier=advice_frontier,
        )

    def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]:
        rows = self._db.execute(
            "SELECT structural_json FROM observation_events "
            "WHERE workspace_commitment = ? ORDER BY id ASC",
            (workspace,),
        ).fetchall()
        result: list[ObservationEnvelope] = []
        for row in rows:
            blob = row[0]
            if type(blob) is not bytes:
                continue
            parsed = strict_json_parse(blob)
            if not isinstance(parsed, Mapping):
                continue
            try:
                result.append(
                    observation_envelope_from_json(
                        JsonObject(cast(Mapping[str, JsonValue], parsed))
                    )
                )
            except Exception:
                continue
        return tuple(result)
