"""SQLite ObservationPort over bundle migration 0002 observation tables."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from typing import Final, cast

import apsw

from yoetz.domain.observation import (
    AdviceSnapshot,
    ObservationContentChunk,
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
from yoetz.domain.values import JsonObject, JsonValue, Timestamp, format_rfc3339_millis
from yoetz.kernel.policies.observation_advice import ObservationCheckFact
from yoetz.ports.objects import ObjectRef
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
                self._db.execute(
                    "UPDATE observation_workspace_bindings SET active=0, revoked_at=? "
                    "WHERE workspace_commitment=? AND active=1",
                    (stamp, command.workspace_commitment),
                )
                self._db.execute(
                    "UPDATE observation_trusted_check_policies "
                    "SET state='revoked', state_token=state_token+1, revoked_at=? "
                    "WHERE workspace_commitment=? AND state='trusted'",
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

    def set_session_advice_snapshot(
        self,
        *,
        workspace: str,
        yoetz_session_id: str,
        snapshot: AdviceSnapshot,
        updated_at: Timestamp,
    ) -> None:
        payload = canonical_encode(advice_snapshot_to_json(snapshot))
        with self._db:
            self._db.execute(
                "INSERT INTO observation_session_advice("
                "workspace_commitment, yoetz_session_id, suppression_identity, "
                "snapshot_json, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(workspace_commitment, yoetz_session_id) DO UPDATE SET "
                "suppression_identity=excluded.suppression_identity, "
                "snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at",
                (
                    workspace,
                    yoetz_session_id,
                    snapshot.suppression_identity,
                    payload,
                    updated_at.wire,
                ),
            )
            # Keep workspace-keyed current row for older readers.
            self._db.execute(
                "INSERT INTO observation_advice(workspace_commitment, suppression_identity, "
                "snapshot_json, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(workspace_commitment) DO UPDATE SET "
                "suppression_identity=excluded.suppression_identity, "
                "snapshot_json=excluded.snapshot_json, updated_at=excluded.updated_at",
                (workspace, snapshot.suppression_identity, payload, updated_at.wire),
            )

    def load_advice_snapshot_for_session(
        self, *, workspace: str, yoetz_session_id: str
    ) -> AdviceSnapshot | None:
        try:
            row = self._db.execute(
                "SELECT snapshot_json FROM observation_session_advice "
                "WHERE workspace_commitment = ? AND yoetz_session_id = ?",
                (workspace, yoetz_session_id),
            ).fetchone()
        except Exception:
            # Pre-0004 bundles: fall back to workspace-scoped advice only.
            return self.load_advice_snapshot(workspace)
        if row is None or type(row[0]) is not bytes:
            return None
        parsed = strict_json_parse(row[0])
        if not isinstance(parsed, Mapping):
            return None
        return advice_snapshot_from_json(JsonObject(cast(Mapping[str, JsonValue], parsed)))

    def codex_session_commitment_for_session(
        self, *, workspace: str, yoetz_session_id: str
    ) -> str | None:
        """Return the mapped Codex session commitment for one Yoetz session, if routed."""

        try:
            row = self._db.execute(
                "SELECT codex_session_commitment FROM observation_workspace_session_routes "
                "WHERE workspace_commitment = ? AND yoetz_session_id = ?",
                (workspace, yoetz_session_id),
            ).fetchone()
        except Exception:
            # Pre-0004 bundles carry no route table; callers fall back to
            # workspace-scoped construction exactly as before.
            return None
        if row is None or type(row[0]) is not str:
            return None
        return row[0]

    def workspace_for_yoetz_session(self, yoetz_session_id: str) -> str | None:
        try:
            row = self._db.execute(
                "SELECT workspace_commitment FROM observation_workspace_session_routes "
                "WHERE yoetz_session_id = ?",
                (yoetz_session_id,),
            ).fetchone()
        except Exception:
            return None
        if row is None or type(row[0]) is not str:
            return None
        return row[0]

    def record_workspace_session_route(
        self,
        *,
        workspace: str,
        yoetz_session_id: str,
        yoetz_task_id: str,
        yoetz_writer_id: str,
        codex_session_commitment: str,
        bound_at: Timestamp,
    ) -> None:
        try:
            with self._db:
                self._db.execute(
                    "UPDATE observation_workspace_session_routes "
                    "SET active=0, unbound_at=? "
                    "WHERE workspace_commitment=? AND yoetz_session_id<>? AND active=1",
                    (bound_at.wire, workspace, yoetz_session_id),
                )
                self._db.execute(
                    "INSERT INTO observation_workspace_session_routes("
                    "workspace_commitment, yoetz_session_id, yoetz_task_id, yoetz_writer_id, "
                    "codex_session_commitment, active, bound_at, unbound_at) "
                    "VALUES(?,?,?,?,?,1,?,NULL) "
                    "ON CONFLICT(workspace_commitment, yoetz_session_id) DO UPDATE SET "
                    "yoetz_task_id=excluded.yoetz_task_id, "
                    "yoetz_writer_id=excluded.yoetz_writer_id, "
                    "codex_session_commitment=excluded.codex_session_commitment, "
                    "active=1, bound_at=excluded.bound_at, unbound_at=NULL",
                    (
                        workspace,
                        yoetz_session_id,
                        yoetz_task_id,
                        yoetz_writer_id,
                        codex_session_commitment,
                        bound_at.wire,
                    ),
                )
        except Exception:
            return

    def record_inspection_snapshot(
        self,
        *,
        workspace: str,
        yoetz_session_id: str,
        subject_state_digest: str,
        changed_paths_digest: str,
        relative_paths: tuple[str, ...],
        facts_object_id: str | None,
        excerpt_object_id: str | None,
        recorded_at: Timestamp,
    ) -> None:
        snapshot_id = (
            "insp_"
            + canonical_digest(
                JsonObject(
                    {
                        "workspace": workspace,
                        "session": yoetz_session_id,
                        "subject": subject_state_digest,
                    }
                )
            ).removeprefix("sha256:")[:48]
        )
        paths_blob = canonical_encode(JsonObject({"relative_paths": list(relative_paths)}))
        try:
            with self._db:
                self._db.execute(
                    "UPDATE observation_inspection_snapshots SET is_current=0 "
                    "WHERE workspace_commitment=? AND yoetz_session_id=? AND is_current=1",
                    (workspace, yoetz_session_id),
                )
                self._db.execute(
                    "INSERT INTO observation_inspection_snapshots("
                    "snapshot_id, workspace_commitment, yoetz_session_id, subject_state_digest, "
                    "changed_paths_digest, relative_paths_json, facts_object_id, "
                    "excerpt_object_id, is_current, recorded_at) "
                    "VALUES(?,?,?,?,?,?,?,?,1,?) "
                    "ON CONFLICT(workspace_commitment, yoetz_session_id, subject_state_digest) "
                    "DO UPDATE SET changed_paths_digest=excluded.changed_paths_digest, "
                    "relative_paths_json=excluded.relative_paths_json, "
                    "facts_object_id=excluded.facts_object_id, "
                    "excerpt_object_id=excluded.excerpt_object_id, "
                    "is_current=1, recorded_at=excluded.recorded_at",
                    (
                        snapshot_id,
                        workspace,
                        yoetz_session_id,
                        subject_state_digest,
                        changed_paths_digest,
                        paths_blob,
                        facts_object_id,
                        excerpt_object_id,
                        recorded_at.wire,
                    ),
                )
        except Exception:
            return

    def load_latest_advice_snapshot(self) -> AdviceSnapshot | None:
        """Deprecated: prefer load_advice_snapshot_for_session / workspace_for_yoetz_session."""

        return None

    def record_advice_history(
        self,
        *,
        workspace: str,
        snapshot: AdviceSnapshot,
        verification_state: str,
        semantic_state: str,
        freshness: str,
        recorded_at: Timestamp,
    ) -> None:
        advice_id = (
            "advice_"
            + canonical_digest(
                JsonObject(
                    {
                        "workspace": workspace,
                        "suppression_identity": snapshot.suppression_identity,
                        "evidence_basis_digest": snapshot.evidence_basis_digest,
                    }
                )
            ).removeprefix("sha256:")[:48]
        )
        with self._db:
            self._db.execute(
                "INSERT INTO observation_advice_history("
                "advice_id,workspace_commitment,subject_frontier,evidence_basis_digest,"
                "suppression_identity,snapshot_json,verification_state,semantic_state,"
                "freshness,recorded_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_commitment,suppression_identity,evidence_basis_digest) "
                "DO NOTHING",
                (
                    advice_id,
                    workspace,
                    snapshot.freshness_frontier,
                    snapshot.evidence_basis_digest,
                    snapshot.suppression_identity,
                    canonical_encode(advice_snapshot_to_json(snapshot)),
                    verification_state,
                    semantic_state,
                    freshness,
                    recorded_at.wire,
                ),
            )

    def _inventory_object(self, ref: ObjectRef) -> None:
        if type(ref) is not ObjectRef:
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "Observation content object is invalid.",
                retryable=False,
            )
        descriptor = (
            ref.metadata.kind.value,
            ref.plaintext_size,
            ref.commitment,
            ref.envelope_digest,
            ref.encryption_format,
            ref.key_slot,
        )
        existing = self._db.execute(
            "SELECT kind,plaintext_size,commitment,envelope_digest,encryption_format,key_slot "
            "FROM objects WHERE object_id=?",
            (ref.object_id,),
        ).fetchone()
        if existing is None:
            self._db.execute(
                "INSERT INTO objects(object_id,kind,plaintext_size,commitment,envelope_digest,"
                "encryption_format,key_slot,state,durable_at) "
                "VALUES(?,?,?,?,?,?,?,'present',?)",
                (
                    ref.object_id,
                    *descriptor,
                    format_rfc3339_millis(ref.metadata.created_at),
                ),
            )
        elif tuple(existing) != descriptor:
            raise _error(
                PublicErrorCode.STORAGE_CORRUPT,
                "Observation content object identity conflicts.",
                retryable=False,
            )

    def record_content_manifest(
        self,
        *,
        workspace: str,
        logical_identity: str,
        chunk: ObservationContentChunk,
        ref: ObjectRef,
        recorded_at: Timestamp,
    ) -> None:
        if type(chunk) is not ObservationContentChunk or type(recorded_at) is not Timestamp:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation content manifest is invalid.",
                retryable=False,
            )
        with self._db:
            self._inventory_object(ref)
            self._db.execute(
                "INSERT INTO observation_content_manifests("
                "object_id,workspace_commitment,logical_identity,content_kind,"
                "correlation_identity,source_commitment,media_type,part_index,part_count,"
                "plaintext_size,content_commitment,redacted,recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_commitment,logical_identity,content_kind,"
                "correlation_identity,source_commitment,part_index) DO NOTHING",
                (
                    ref.object_id,
                    workspace,
                    logical_identity,
                    chunk.content_kind.value,
                    chunk.correlation_identity,
                    chunk.source_commitment,
                    chunk.media_type,
                    chunk.part_index,
                    chunk.part_count,
                    ref.plaintext_size,
                    ref.commitment,
                    int(chunk.redacted),
                    recorded_at.wire,
                ),
            )
            row = self._db.execute(
                "SELECT object_id,part_count,content_commitment,redacted "
                "FROM observation_content_manifests "
                "WHERE workspace_commitment=? AND logical_identity=? AND content_kind=? "
                "AND correlation_identity=? AND source_commitment=? AND part_index=?",
                (
                    workspace,
                    logical_identity,
                    chunk.content_kind.value,
                    chunk.correlation_identity,
                    chunk.source_commitment,
                    chunk.part_index,
                ),
            ).fetchone()
            if row != (ref.object_id, chunk.part_count, ref.commitment, int(chunk.redacted)):
                raise _error(
                    PublicErrorCode.STORAGE_CORRUPT,
                    "Observation content manifest conflicts.",
                    retryable=False,
                )

    def content_manifest_object_id(
        self,
        *,
        workspace: str,
        logical_identity: str,
        chunk: ObservationContentChunk,
    ) -> str | None:
        row = self._db.execute(
            "SELECT object_id FROM observation_content_manifests "
            "WHERE workspace_commitment=? AND logical_identity=? AND content_kind=? "
            "AND correlation_identity=? AND source_commitment=? AND part_index=?",
            (
                workspace,
                logical_identity,
                chunk.content_kind.value,
                chunk.correlation_identity,
                chunk.source_commitment,
                chunk.part_index,
            ),
        ).fetchone()
        return cast(str, row[0]) if row is not None and type(row[0]) is str else None

    def bind_workspace_locator(
        self,
        *,
        workspace: str,
        locator_ref: ObjectRef,
        bound_at: Timestamp,
    ) -> None:
        with self._db:
            self._inventory_object(locator_ref)
            self._db.execute(
                "INSERT INTO observation_workspace_bindings("
                "workspace_commitment,locator_object_id,active,bound_at,revoked_at) "
                "VALUES(?,?,1,?,NULL) "
                "ON CONFLICT(workspace_commitment) DO UPDATE SET "
                "locator_object_id=excluded.locator_object_id,active=1,"
                "bound_at=excluded.bound_at,revoked_at=NULL",
                (workspace, locator_ref.object_id, bound_at.wire),
            )

    def workspace_locator_descriptor(self, workspace: str) -> tuple[str, str] | None:
        row = self._db.execute(
            "SELECT binding.locator_object_id,objects.envelope_digest "
            "FROM observation_workspace_bindings AS binding "
            "JOIN objects ON objects.object_id=binding.locator_object_id "
            "WHERE binding.workspace_commitment=? AND binding.active=1",
            (workspace,),
        ).fetchone()
        if row is None or type(row[0]) is not str or type(row[1]) is not str:
            return None
        return cast(str, row[0]), cast(str, row[1])

    def record_trusted_check_policy(
        self,
        *,
        workspace: str,
        policy_digest: str,
        trust_ref: ObjectRef,
        trusted_at: Timestamp,
    ) -> None:
        with self._db:
            self._inventory_object(trust_ref)
            self._db.execute(
                "UPDATE observation_trusted_check_policies SET state='superseded',"
                "revoked_at=? WHERE workspace_commitment=? AND state='trusted' "
                "AND policy_digest<>?",
                (trusted_at.wire, workspace, policy_digest),
            )
            token_row = self._db.execute(
                "SELECT COALESCE(MAX(state_token),0)+1 "
                "FROM observation_trusted_check_policies WHERE workspace_commitment=?",
                (workspace,),
            ).fetchone()
            token = int(token_row[0]) if token_row is not None else 1
            self._db.execute(
                "INSERT INTO observation_trusted_check_policies("
                "workspace_commitment,policy_digest,trust_object_id,state,trusted_at,"
                "revoked_at,state_token) VALUES(?,?,?,'trusted',?,NULL,?) "
                "ON CONFLICT(workspace_commitment,policy_digest) DO UPDATE SET "
                "trust_object_id=excluded.trust_object_id,state='trusted',"
                "trusted_at=excluded.trusted_at,revoked_at=NULL",
                (
                    workspace,
                    policy_digest,
                    trust_ref.object_id,
                    trusted_at.wire,
                    token,
                ),
            )

    def policy_digest_is_trusted(self, workspace: str, policy_digest: str) -> bool:
        return (
            self._db.execute(
                "SELECT 1 FROM observation_trusted_check_policies "
                "WHERE workspace_commitment=? AND policy_digest=? AND state='trusted'",
                (workspace, policy_digest),
            ).fetchone()
            is not None
        )

    def latest_verification_subject_digest(self, workspace: str) -> str | None:
        row = self._db.execute(
            "SELECT subject_state_digest FROM observation_verification_jobs "
            "WHERE workspace_commitment=? ORDER BY state_token DESC LIMIT 1",
            (workspace,),
        ).fetchone()
        return cast(str, row[0]) if row is not None and type(row[0]) is str else None

    def load_check_facts(self, workspace: str) -> tuple[ObservationCheckFact, ...]:
        rows = self._db.execute(
            "SELECT jobs.approval_commitment,jobs.subject_state_digest,results.status,"
            "jobs.state_token,results.is_current "
            "FROM observation_verification_results AS results "
            "JOIN observation_verification_jobs AS jobs ON jobs.job_id=results.job_id "
            "WHERE results.workspace_commitment=? "
            "ORDER BY jobs.state_token,results.check_id",
            (workspace,),
        ).fetchall()
        return tuple(
            ObservationCheckFact(
                approval_commitment=cast(str, row[0]),
                subject_state_digest=cast(str, row[1]),
                status=cast(str, row[2]),
                cursor_event_position=cast(int, row[3]),
                is_current=bool(row[4]),
            )
            for row in rows
        )

    def verification_repository(self):
        from yoetz.adapters.sqlite.observation_verification import (
            SqliteObservationVerificationRepository,
        )

        return SqliteObservationVerificationRepository(self._db)

    def record_logical_identity_claim(
        self,
        *,
        workspace: str,
        logical_identity: str,
        materialization_digest: str,
        operation_id: str,
        source_mask: int,
        mapping_version: str,
        materialized_at: Timestamp,
    ) -> None:
        if type(source_mask) is not int or source_mask not in {1, 2}:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation source mask is invalid.",
                retryable=False,
            )
        with self._db:
            existing = self._db.execute(
                "SELECT canonical_materialization_digest,operation_id,source_mask,mapping_version "
                "FROM observation_logical_identity "
                "WHERE workspace_commitment=? AND logical_identity=?",
                (workspace, logical_identity),
            ).fetchone()
            if existing is None:
                self._db.execute(
                    "INSERT INTO observation_logical_identity("
                    "workspace_commitment,logical_identity,canonical_materialization_digest,"
                    "operation_id,source_mask,mapping_version,materialized_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        workspace,
                        logical_identity,
                        materialization_digest,
                        operation_id,
                        source_mask,
                        mapping_version,
                        materialized_at.wire,
                    ),
                )
                return
            if (
                existing[0] != materialization_digest
                or existing[1] != operation_id
                or existing[3] != mapping_version
            ):
                raise _error(
                    PublicErrorCode.STORAGE_CORRUPT,
                    "Observation logical identity conflicts.",
                    retryable=False,
                )
            combined = cast(int, existing[2]) | source_mask
            if combined != existing[2]:
                self._db.execute(
                    "UPDATE observation_logical_identity SET source_mask=? "
                    "WHERE workspace_commitment=? AND logical_identity=?",
                    (combined, workspace, logical_identity),
                )

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

    def _last_receipt(self, workspace: str, *, session_commitment: str | None = None) -> str | None:
        if session_commitment is None:
            row = self._db.execute(
                "SELECT receipt_time FROM observation_events WHERE workspace_commitment = ? "
                "ORDER BY id DESC LIMIT 1",
                (workspace,),
            ).fetchone()
        else:
            row = self._db.execute(
                "SELECT receipt_time FROM observation_events WHERE workspace_commitment = ? "
                "AND session_commitment = ? ORDER BY id DESC LIMIT 1",
                (workspace, session_commitment),
            ).fetchone()
        return cast(str, row[0]) if row is not None and type(row[0]) is str else None

    def _status_unlocked(
        self, workspace_commitment: str, *, session_commitment: str | None = None
    ) -> ObservationStatus:
        consent = self._consent_row(workspace_commitment)
        coverage = {
            ObservationSource.CODEX_HOOK: False,
            ObservationSource.CODEX_SESSION_STREAM: False,
        }
        gaps: set[str] = set()
        unsupported: set[str] = set()
        if session_commitment is None:
            rows = self._db.execute(
                "SELECT session_commitment, source, event_kind, gap_codes_json, "
                "content_refs_json FROM observation_events "
                "WHERE workspace_commitment = ? ORDER BY id ASC",
                (workspace_commitment,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT session_commitment, source, event_kind, gap_codes_json, "
                "content_refs_json FROM observation_events "
                "WHERE workspace_commitment = ? AND session_commitment = ? ORDER BY id ASC",
                (workspace_commitment, session_commitment),
            ).fetchall()
        # Event rows are append-only, but ``gap_codes`` describe the condition
        # observed at that row rather than an everlasting current state. Keep a
        # small per-session current projection while retaining all rows for the
        # operator history. In particular, a later accepted observation is live
        # evidence that an earlier source-lag/cursor-stale condition healed;
        # another session's healthy event must never clear this session's gap.
        current_by_session: dict[str, set[str]] = {}
        unsupported_by_session: dict[str, set[str]] = {}
        for row in cast("Iterable[tuple[object, ...]]", rows):
            row_session = row[0]
            source = ObservationSource(cast(str, row[1]))
            event_kind = cast(str, row[2])
            if type(row_session) is not str:
                continue
            session_current = current_by_session.setdefault(row_session, set())
            session_unsupported = unsupported_by_session.setdefault(row_session, set())
            coverage[source] = True
            gap_codes: set[str] = set()
            gap_blob = row[3]
            if type(gap_blob) is bytes:
                parsed = strict_json_parse(gap_blob)
                if type(parsed) is list:
                    gap_codes = {item for item in cast(list[object], parsed) if type(item) is str}
            # A synthetic observation_gap row records the failure itself and
            # must not be mistaken for recovery. Any accepted envelope after
            # it advances the session's source and clears only the transient
            # conditions whose healing the observation authority can prove.
            if event_kind != "observation_gap":
                session_current.discard(ObservationGapCode.SOURCE_LAG.value)
                session_current.discard(ObservationGapCode.CURSOR_STALE.value)
                refs_blob = row[4]
                if type(refs_blob) is bytes:
                    refs = strict_json_parse(refs_blob)
                    if type(refs) is list and refs:
                        session_current.discard(
                            ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value
                        )
            session_current.update(gap_codes)
            if ObservationGapCode.UNSUPPORTED_EVENT.value in gap_codes:
                session_unsupported.add(event_kind)
        if session_commitment is None:
            for session_current in current_by_session.values():
                gaps.update(session_current)
            for session_unsupported in unsupported_by_session.values():
                unsupported.update(session_unsupported)
        else:
            gaps.update(current_by_session.get(session_commitment, ()))
            unsupported.update(unsupported_by_session.get(session_commitment, ()))
        if consent is None:
            lifecycle = ObservationLifecycle.STOPPED
        elif consent[0] is not None:
            lifecycle = ObservationLifecycle.STOPPED
        elif consent[1]:
            lifecycle = ObservationLifecycle.STOPPED
        elif not any(coverage.values()):
            lifecycle = ObservationLifecycle.DEGRADED
        elif gaps or unsupported:
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
        last = self._last_receipt(workspace_commitment, session_commitment=session_commitment)
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
        return self._envelopes_from_rows(rows)

    def list_envelopes_for_session(
        self, workspace: str, session_commitment: str
    ) -> tuple[ObservationEnvelope, ...]:
        """Return only the mapped session's retained envelopes (#352).

        Task-scoped advice construction must not see other sessions' envelopes:
        a workspace retains history for every consented session, and building a
        mapped session snapshot from all of it re-attributes unrelated
        degradation to the current task — the exact #249/#250 failure mode one
        layer down.
        """

        rows = self._db.execute(
            "SELECT structural_json FROM observation_events "
            "WHERE workspace_commitment = ? AND session_commitment = ? ORDER BY id ASC",
            (workspace, session_commitment),
        ).fetchall()
        return self._envelopes_from_rows(rows)

    async def status_for_session(
        self, workspace: str, session_commitment: str
    ) -> ObservationStatus:
        """Session-scoped lifecycle/gap/coverage health for mapped advice inputs (#352)."""

        async with self._lock:
            return self._status_unlocked(workspace, session_commitment=session_commitment)

    @staticmethod
    def _envelopes_from_rows(rows: Iterable[object]) -> tuple[ObservationEnvelope, ...]:
        result: list[ObservationEnvelope] = []
        for row in cast("Iterable[tuple[object, ...]]", rows):
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
