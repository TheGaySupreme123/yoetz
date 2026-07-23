"""Owner-private local observation consent, binding, and structural ingest state.

This store backs hook and ``yoetz observe`` controls when the service observation
handlers are unavailable. It retains allowlisted structure and commitments only —
never transcript prose or raw workspace paths.
"""

from __future__ import annotations

import base64
import contextlib
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.domain.observation import (
    AdviceSnapshot,
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
    advice_snapshot_from_json,
    advice_snapshot_to_json,
    observation_cursor_from_json,
    observation_cursor_to_json,
    observation_envelope_from_json,
    observation_envelope_to_json,
    workspace_commitment_from_path,
)
from yoetz.domain.values import JsonObject, JsonValue, Timestamp, timestamp_from_datetime
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError

__all__ = [
    "HOOK_MAPPING_VERSION",
    "LocalObservationConsent",
    "LocalObservationStore",
    "STREAM_MAPPING_VERSION",
    "YOETZ_TOOL_NAMES",
    "observation_dir",
    "session_commitment_from_codex_id",
    "workspace_commitment_for_path",
]

HOOK_MAPPING_VERSION: Final = "codex-obs-hook/1.0.0"
STREAM_MAPPING_VERSION: Final = "codex-obs-stream/1.0.0"
_KEY_BYTES: Final = 32
_MAX_STATE_BYTES: Final = 1_048_576
_MAX_ENVELOPES: Final = 256
_MAX_DEDUP: Final = 4_096
_MAX_OPEN_PRE: Final = 256
_MAX_OUTBOX: Final = 512
_MAX_HOOK_SEQUENCES: Final = 256

YOETZ_TOOL_NAMES: Final = frozenset(
    {
        "start",
        "status",
        "check",
        "publish_work",
        "receipt",
        "respond",
        "mcp__yoetz__start",
        "mcp__yoetz__status",
        "mcp__yoetz__check",
        "mcp__yoetz__publish_work",
        "mcp__yoetz__receipt",
        "mcp__yoetz__respond",
    }
)

_SESSION_DOMAIN: Final = b"yoetz/observation-session/v1\x00"


def _error(code: PublicErrorCode, message: str, *, retryable: bool) -> PublicOperationError:
    return PublicOperationError(code, message, retryable)


def _now() -> Timestamp:
    current = datetime.now(UTC)
    stamp = current.replace(microsecond=(current.microsecond // 1000) * 1000)
    return timestamp_from_datetime(stamp)


def _ensure_dir(path: Path) -> None:
    try:
        ensure_owner_only_dir(path)
    except PathSafetyError:
        if not path.is_dir() or path.is_symlink():
            raise
        mode = path.stat().st_mode & 0o777
        if mode != 0o700:
            raise


def observation_dir(*, _state: Path | None = None) -> Path:
    """Return the private observation state directory under the Yoetz state root."""

    root = state_dir() if _state is None else _state
    path = root / "observation"
    _ensure_dir(root)
    _ensure_dir(path)
    return path


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short_write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def _read_bytes(path: Path, *, maximum: int) -> bytes | None:
    try:
        if not path.is_file() or path.is_symlink():
            return None
        size = path.stat().st_size
        if size <= 0 or size > maximum:
            return None
        data = path.read_bytes()
        if len(data) > maximum:
            return None
        return data
    except OSError:
        return None


@dataclass(frozen=True, slots=True)
class LocalObservationConsent:
    workspace_commitment: str
    granted_at: Timestamp
    revoked_at: Timestamp | None = None
    paused: bool = False

    @property
    def active(self) -> bool:
        return self.revoked_at is None and not self.paused


@dataclass
class _WorkspaceState:
    consent: LocalObservationConsent | None = None
    session_workspaces: dict[str, str] | None = None
    cursors: dict[str, ObservationCursor] | None = None
    dedup: set[str] | None = None
    envelopes: list[ObservationEnvelope] | None = None
    gaps: set[str] | None = None
    unsupported_events: set[str] | None = None
    last_receipt: Timestamp | None = None
    advice_frontier: str | None = None
    advice_snapshot: AdviceSnapshot | None = None
    last_advice_suppression: str | None = None
    open_pre: dict[str, str] | None = None
    stream_cursors: dict[str, ObservationCursor] | None = None
    stream_partials: dict[str, bytes] | None = None
    hook_sequences: dict[str, int] | None = None
    last_stream_reconcile_mono_ms: int | None = None
    last_hook_receipt_mono_ms: int | None = None
    last_successful_drain_mono_ms: int | None = None
    pending_outbox: list[tuple[str, ObservationEnvelope]] | None = None
    codex_session_bindings: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if self.session_workspaces is None:
            self.session_workspaces = {}
        if self.cursors is None:
            self.cursors = {}
        if self.dedup is None:
            self.dedup = set()
        if self.envelopes is None:
            self.envelopes = []
        if self.gaps is None:
            self.gaps = set()
        if self.unsupported_events is None:
            self.unsupported_events = set()
        if self.open_pre is None:
            self.open_pre = {}
        if self.stream_cursors is None:
            self.stream_cursors = {}
        if self.stream_partials is None:
            self.stream_partials = {}
        if self.hook_sequences is None:
            self.hook_sequences = {}
        if self.pending_outbox is None:
            self.pending_outbox = []
        if self.codex_session_bindings is None:
            self.codex_session_bindings = {}


def _cursor_key(source: ObservationSource, session_commitment: str) -> str:
    return f"{source.value}:{session_commitment}"


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


class LocalObservationStore:
    """Durable ObservationPort-shaped local store for consent and structural envelopes."""

    def __init__(
        self,
        *,
        _state: Path | None = None,
        _monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._root = observation_dir(_state=_state)
        self._lock = threading.RLock()
        self._monotonic = _monotonic

    def _now_mono(self) -> float:
        import time

        return time.monotonic() if self._monotonic is None else self._monotonic()

    def key_material(self) -> bytes:
        path = self._root / "key-material.bin"
        existing = _read_bytes(path, maximum=_KEY_BYTES)
        if existing is not None and len(existing) == _KEY_BYTES:
            return existing
        material = os.urandom(_KEY_BYTES)
        _atomic_write(path, material)
        return material

    def workspace_commitment(self, path: str) -> str:
        return workspace_commitment_from_path(self.key_material(), path)

    def session_commitment(self, codex_session_id: str) -> str:
        return session_commitment_from_codex_id(self.key_material(), codex_session_id)

    def grant_consent(self, workspace_commitment: str, granted_at: Timestamp | None = None) -> None:
        with self._lock:
            state = self._load(workspace_commitment)
            stamp = granted_at if granted_at is not None else _now()
            state.consent = LocalObservationConsent(
                workspace_commitment=workspace_commitment,
                granted_at=stamp,
                revoked_at=None,
                paused=False,
            )
            self._save(workspace_commitment, state)

    def bind_session(self, workspace_commitment: str, session_commitment: str) -> None:
        with self._lock:
            state = self._load(workspace_commitment)
            if state.consent is None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is missing.",
                    retryable=False,
                )
            assert state.session_workspaces is not None
            existing = state.session_workspaces.get(session_commitment)
            if existing is not None and existing != workspace_commitment:
                raise _error(
                    PublicErrorCode.SESSION_CONFLICT,
                    "Observation session is already bound.",
                    retryable=False,
                )
            state.session_workspaces[session_commitment] = workspace_commitment
            self._save(workspace_commitment, state)

    def bind_codex_session(self, workspace_commitment: str, codex_session_id: str) -> str:
        """Bind a Codex session id to a consented workspace; return session commitment."""

        session = self.session_commitment(codex_session_id)
        self.bind_session(workspace_commitment, session)
        with self._lock:
            state = self._load(workspace_commitment)
            assert state.codex_session_bindings is not None
            state.codex_session_bindings[codex_session_id] = session
            self._save(workspace_commitment, state)
        return session

    def find_workspace_for_codex_session(self, codex_session_id: str) -> str | None:
        with self._lock:
            for workspace, state in self._iter_workspaces():
                assert state.codex_session_bindings is not None
                if codex_session_id in state.codex_session_bindings:
                    consent = state.consent
                    if consent is not None and consent.active:
                        return workspace
            # Single active consent may auto-bind later at ingest.
            active = [
                workspace
                for workspace, state in self._iter_workspaces()
                if state.consent is not None and state.consent.active
            ]
            if len(active) == 1:
                return active[0]
            return None

    def consent_for(self, workspace_commitment: str) -> LocalObservationConsent | None:
        with self._lock:
            return self._load(workspace_commitment).consent

    def list_consented_workspaces(self) -> tuple[str, ...]:
        with self._lock:
            result = [
                workspace
                for workspace, state in self._iter_workspaces()
                if state.consent is not None
            ]
            return tuple(sorted(result, key=str.encode))

    def note_open_pre(self, workspace: str, correlation_id: str, event_kind: str) -> None:
        with self._lock:
            state = self._load(workspace)
            assert state.open_pre is not None
            if len(state.open_pre) >= _MAX_OPEN_PRE:
                # Drop oldest insertion order by rebuilding from remaining items.
                oldest = next(iter(state.open_pre))
                del state.open_pre[oldest]
            state.open_pre[correlation_id] = event_kind
            self._save(workspace, state)

    def consume_open_pre(self, workspace: str, correlation_id: str) -> str | None:
        with self._lock:
            state = self._load(workspace)
            assert state.open_pre is not None
            kind = state.open_pre.pop(correlation_id, None)
            self._save(workspace, state)
            return kind

    def has_open_pre(self, workspace: str, correlation_id: str) -> bool:
        with self._lock:
            state = self._load(workspace)
            assert state.open_pre is not None
            return correlation_id in state.open_pre

    def set_advice_snapshot(self, workspace: str, snapshot: AdviceSnapshot | None) -> None:
        with self._lock:
            state = self._load(workspace)
            state.advice_snapshot = snapshot
            state.advice_frontier = None if snapshot is None else snapshot.freshness_frontier
            self._save(workspace, state)

    def peek_advice_for_delivery(self, workspace: str) -> AdviceSnapshot | None:
        """Return a new high-value advice snapshot once per suppression identity."""

        with self._lock:
            state = self._load(workspace)
            snapshot = state.advice_snapshot
            if snapshot is None:
                return None
            if state.last_advice_suppression == snapshot.suppression_identity:
                return None
            if not snapshot.ranked_finding_ids:
                return None
            state.last_advice_suppression = snapshot.suppression_identity
            self._save(workspace, state)
            return snapshot

    def advice_snapshot_for(self, workspace: str) -> AdviceSnapshot | None:
        """Non-consuming read of the current advice snapshot for status views."""

        with self._lock:
            return self._load(workspace).advice_snapshot

    def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]:
        with self._lock:
            state = self._load(workspace)
            assert state.envelopes is not None
            return tuple(state.envelopes)

    def refresh_advice(
        self,
        workspace: str,
        *,
        composition: object | None = None,
        check_facts: object = (),
        inspect_fact: object | None = None,
        plan_path_digests: object = (),
        semantic_addon: object | None = None,
    ) -> AdviceSnapshot | None:
        """Recompute deterministic (and optional semantic) advice from retained envelopes."""

        from yoetz.application.observation_advice import (
            ObservationAdviceBuildInput,
            ObservationAdviceSemanticAddon,
            build_observation_advice_snapshot,
        )
        from yoetz.kernel.policies.observation_advice import (
            ObservationCheckFact,
            ObservationCompositionFact,
            ObservationInspectFact,
        )

        with self._lock:
            state = self._load(workspace)
            assert state.envelopes is not None
            assert state.gaps is not None
            status = self._status_unlocked(workspace)
            typed_checks: tuple[ObservationCheckFact, ...] = ()
            if type(check_facts) is tuple:
                typed_checks = tuple(
                    item
                    for item in cast(tuple[object, ...], check_facts)
                    if type(item) is ObservationCheckFact
                )
            typed_inspect = inspect_fact if type(inspect_fact) is ObservationInspectFact else None
            typed_composition = (
                composition if type(composition) is ObservationCompositionFact else None
            )
            typed_plans: tuple[str, ...] = ()
            if type(plan_path_digests) is tuple:
                typed_plans = tuple(
                    item
                    for item in cast(tuple[object, ...], plan_path_digests)
                    if type(item) is str
                )
            typed_semantic = (
                semantic_addon if type(semantic_addon) is ObservationAdviceSemanticAddon else None
            )
            snapshot = build_observation_advice_snapshot(
                ObservationAdviceBuildInput(
                    envelopes=tuple(state.envelopes),
                    lifecycle=status.lifecycle,
                    gaps=tuple(sorted(state.gaps, key=str.encode)),
                    check_facts=typed_checks,
                    inspect_fact=typed_inspect,
                    composition=typed_composition,
                    plan_path_digests=typed_plans,
                    prior_snapshot=state.advice_snapshot,
                    semantic_addon=typed_semantic,
                    has_real_observation=bool(state.envelopes),
                )
            )
            state.advice_snapshot = snapshot
            state.advice_frontier = None if snapshot is None else snapshot.freshness_frontier
            self._save(workspace, state)
            return snapshot

    def get_stream_cursor(
        self, workspace: str, session_commitment: str
    ) -> ObservationCursor | None:
        with self._lock:
            state = self._load(workspace)
            assert state.stream_cursors is not None
            return state.stream_cursors.get(session_commitment)

    def set_stream_cursor(
        self, workspace: str, session_commitment: str, cursor: ObservationCursor
    ) -> None:
        with self._lock:
            state = self._load(workspace)
            assert state.stream_cursors is not None
            state.stream_cursors[session_commitment] = cursor
            self._save(workspace, state)

    def get_stream_partial(self, workspace: str, session_commitment: str) -> bytes:
        with self._lock:
            state = self._load(workspace)
            assert state.stream_partials is not None
            return state.stream_partials.get(session_commitment, b"")

    def set_stream_partial(
        self, workspace: str, session_commitment: str, partial: bytes
    ) -> None:
        if type(partial) is not bytes or len(partial) > 262_144:
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            assert state.stream_partials is not None
            if partial:
                state.stream_partials[session_commitment] = partial
            else:
                state.stream_partials.pop(session_commitment, None)
            self._save(workspace, state)

    def note_stream_reconcile(self, workspace: str, *, mono: float | None = None) -> None:
        import time

        with self._lock:
            state = self._load(workspace)
            current = time.monotonic() if mono is None else mono
            state.last_stream_reconcile_mono_ms = int(current * 1000)
            self._save(workspace, state)

    def last_stream_reconcile_mono(self, workspace: str) -> float | None:
        with self._lock:
            value = self._load(workspace).last_stream_reconcile_mono_ms
            return None if value is None else value / 1000.0

    def allocate_hook_ordinal(self, workspace: str, session_commitment: str) -> int:
        """Allocate a durable per-session hook sequence when the host supplies no ordinal."""

        with self._lock:
            state = self._load(workspace)
            assert state.hook_sequences is not None
            next_value = state.hook_sequences.get(session_commitment, 0) + 1
            state.hook_sequences[session_commitment] = next_value
            # Bound retained sequence keys.
            if len(state.hook_sequences) > _MAX_HOOK_SEQUENCES:
                oldest = next(iter(state.hook_sequences))
                del state.hook_sequences[oldest]
            self._save(workspace, state)
            return next_value

    def enqueue_outbox(
        self, workspace: str, codex_session_id: str, envelope: ObservationEnvelope
    ) -> str | None:
        """Queue a structural envelope for service drain. Returns overflow gap or None."""

        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            assert state.gaps is not None
            if len(state.pending_outbox) >= _MAX_OUTBOX:
                state.gaps.add(ObservationGapCode.OUTBOX_OVERFLOW.value)
                self._save(workspace, state)
                return ObservationGapCode.OUTBOX_OVERFLOW.value
            # Dedup identical source identities already pending for this session.
            for pending_session, pending_envelope in state.pending_outbox:
                if (
                    pending_session == codex_session_id
                    and pending_envelope.source_identity == envelope.source_identity
                    and pending_envelope.event_kind == envelope.event_kind
                    and pending_envelope.cursor.event_position == envelope.cursor.event_position
                ):
                    return None
            state.pending_outbox.append((codex_session_id, envelope))
            self._save(workspace, state)
            return None

    def list_pending_outbox(
        self, workspace: str, *, codex_session_id: str | None = None
    ) -> tuple[tuple[str, ObservationEnvelope], ...]:
        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            if codex_session_id is None:
                return tuple(state.pending_outbox)
            return tuple(
                item for item in state.pending_outbox if item[0] == codex_session_id
            )

    def pending_outbox_count(self, workspace: str) -> int:
        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            return len(state.pending_outbox)

    def acknowledge_outbox(
        self, workspace: str, codex_session_id: str, source_identity: str
    ) -> bool:
        """Remove one outbox entry after the task-bundle transaction has committed."""

        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            for index, (session, envelope) in enumerate(state.pending_outbox):
                if session == codex_session_id and envelope.source_identity == source_identity:
                    del state.pending_outbox[index]
                    state.last_successful_drain_mono_ms = int(self._now_mono() * 1000)
                    self._save(workspace, state)
                    return True
            return False

    def note_coverage_gap(self, workspace: str, gap_code: str) -> None:
        """Record a safe local coverage gap without retaining payload prose."""

        with self._lock:
            state = self._load(workspace)
            assert state.gaps is not None
            if type(gap_code) is str and gap_code:
                state.gaps.add(gap_code)
            self._save(workspace, state)

    def ingest(self, envelope: ObservationEnvelope) -> ObservationIngestResult:
        if type(envelope) is not ObservationEnvelope:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation envelope is invalid.",
                retryable=False,
            )
        with self._lock:
            try:
                workspace = self._workspace_for_envelope(envelope)
            except PublicOperationError:
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_MISSING.value,
                    None,
                )
            state = self._load(workspace)
            consent = state.consent
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
            assert state.dedup is not None
            assert state.cursors is not None
            assert state.envelopes is not None
            assert state.gaps is not None
            assert state.unsupported_events is not None
            key = _dedup_key(workspace, envelope)
            if key in state.dedup:
                cursor = state.cursors.get(
                    _cursor_key(envelope.source, envelope.session_commitment)
                )
                return ObservationIngestResult(
                    ObservationIngestDisposition.DUPLICATE,
                    "duplicate",
                    cursor,
                )
            cursor_key = _cursor_key(envelope.source, envelope.session_commitment)
            existing = state.cursors.get(cursor_key)
            if existing is not None and envelope.cursor.is_stale_relative_to(existing):
                state.gaps.add(ObservationGapCode.CURSOR_STALE.value)
                self._save(workspace, state)
                return ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CURSOR_STALE.value,
                    existing,
                )
            state.dedup.add(key)
            if len(state.dedup) > _MAX_DEDUP:
                # Bounded retention: drop an arbitrary oldest-looking member.
                state.dedup.pop()
            state.cursors[cursor_key] = envelope.cursor
            state.envelopes.append(envelope)
            if len(state.envelopes) > _MAX_ENVELOPES:
                del state.envelopes[: len(state.envelopes) - _MAX_ENVELOPES]
            state.last_receipt = envelope.receipt_time
            mono_ms = int(self._now_mono() * 1000)
            if envelope.source is ObservationSource.CODEX_HOOK:
                state.last_hook_receipt_mono_ms = mono_ms
            else:
                state.last_stream_reconcile_mono_ms = mono_ms
            for gap in envelope.gap_codes:
                state.gaps.add(gap)
            if ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes:
                state.unsupported_events.add(envelope.event_kind)
            self._save(workspace, state)
            return ObservationIngestResult(
                ObservationIngestDisposition.ACCEPTED,
                None,
                envelope.cursor,
            )

    def status(self, query: ObservationStatusQuery) -> ObservationStatus:
        with self._lock:
            return self._status_unlocked(query.workspace_commitment)

    def pause(self, command: ObservationControlCommand) -> ObservationStatus:
        with self._lock:
            state = self._load(command.workspace_commitment)
            consent = state.consent
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
            state.consent = LocalObservationConsent(
                workspace_commitment=consent.workspace_commitment,
                granted_at=consent.granted_at,
                revoked_at=consent.revoked_at,
                paused=True,
            )
            self._save(command.workspace_commitment, state)
            return self._status_unlocked(command.workspace_commitment)

    def resume(self, command: ObservationControlCommand) -> ObservationStatus:
        with self._lock:
            state = self._load(command.workspace_commitment)
            consent = state.consent
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
            state.consent = LocalObservationConsent(
                workspace_commitment=consent.workspace_commitment,
                granted_at=consent.granted_at,
                revoked_at=None,
                paused=False,
            )
            self._save(command.workspace_commitment, state)
            return self._status_unlocked(command.workspace_commitment)

    def revoke(self, command: ObservationRevokeCommand) -> ObservationStatus:
        with self._lock:
            state = self._load(command.workspace_commitment)
            consent = state.consent
            if consent is None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is missing.",
                    retryable=False,
                )
            revoked_at = (
                state.last_receipt if state.last_receipt is not None else consent.granted_at
            )
            state.consent = LocalObservationConsent(
                workspace_commitment=consent.workspace_commitment,
                granted_at=consent.granted_at,
                revoked_at=revoked_at,
                paused=True,
            )
            self._save(command.workspace_commitment, state)
            return self._status_unlocked(command.workspace_commitment)

    def _workspace_path(self, workspace_commitment: str) -> Path:
        digest = workspace_commitment.removeprefix("hmac-sha256:")
        if len(digest) != 64:
            raise ProtocolValueError("invalid_commitment")
        return self._root / "workspaces" / f"{digest}.json"

    def _iter_workspaces(self) -> list[tuple[str, _WorkspaceState]]:
        directory = self._root / "workspaces"
        if not directory.is_dir():
            return []
        result: list[tuple[str, _WorkspaceState]] = []
        for path in directory.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            digest = path.stem
            workspace = f"hmac-sha256:{digest}"
            result.append((workspace, self._load(workspace)))
        return result

    def _load(self, workspace_commitment: str) -> _WorkspaceState:
        path = self._workspace_path(workspace_commitment)
        raw = _read_bytes(path, maximum=_MAX_STATE_BYTES)
        if raw is None:
            return _WorkspaceState()
        try:
            parsed = strict_json_parse(raw)
        except ProtocolValueError:
            return _WorkspaceState()
        if not isinstance(parsed, Mapping):
            return _WorkspaceState()
        return self._state_from_json(cast(Mapping[str, JsonValue], parsed))

    def _save(self, workspace_commitment: str, state: _WorkspaceState) -> None:
        directory = self._root / "workspaces"
        _ensure_dir(directory)
        path = self._workspace_path(workspace_commitment)
        payload = canonical_encode(self._state_to_json(workspace_commitment, state)) + b"\n"
        if len(payload) > _MAX_STATE_BYTES:
            # Drop oldest envelopes to stay bounded.
            assert state.envelopes is not None
            while state.envelopes and len(payload) > _MAX_STATE_BYTES:
                del state.envelopes[0]
                payload = canonical_encode(self._state_to_json(workspace_commitment, state)) + b"\n"
        _atomic_write(path, payload)

    def _workspace_for_envelope(self, envelope: ObservationEnvelope) -> str:
        for workspace, state in self._iter_workspaces():
            assert state.session_workspaces is not None
            bound = state.session_workspaces.get(envelope.session_commitment)
            if bound is not None:
                return bound
        active = [
            workspace
            for workspace, state in self._iter_workspaces()
            if state.consent is not None and state.consent.active
        ]
        if len(active) == 1:
            workspace = active[0]
            state = self._load(workspace)
            assert state.session_workspaces is not None
            state.session_workspaces[envelope.session_commitment] = workspace
            self._save(workspace, state)
            return workspace
        raise _error(
            PublicErrorCode.INVALID_REQUEST,
            "Observation workspace consent is missing.",
            retryable=False,
        )

    def _status_unlocked(self, workspace_commitment: str) -> ObservationStatus:
        from yoetz.application.observation_health import (
            DEFAULT_OBSERVATION_HEALTH_THRESHOLDS,
            ObservationHealthSignals,
            compute_observation_lifecycle,
        )

        state = self._load(workspace_commitment)
        consent = state.consent
        coverage = {
            ObservationSource.CODEX_HOOK: False,
            ObservationSource.CODEX_SESSION_STREAM: False,
        }
        assert state.envelopes is not None
        assert state.gaps is not None
        assert state.unsupported_events is not None
        assert state.pending_outbox is not None
        assert state.session_workspaces is not None
        for envelope in state.envelopes:
            coverage[envelope.source] = True
        pending = len(state.pending_outbox)
        last_hook = (
            None
            if state.last_hook_receipt_mono_ms is None
            else state.last_hook_receipt_mono_ms / 1000.0
        )
        last_stream = (
            None
            if state.last_stream_reconcile_mono_ms is None
            else state.last_stream_reconcile_mono_ms / 1000.0
        )
        last_drain = (
            None
            if state.last_successful_drain_mono_ms is None
            else state.last_successful_drain_mono_ms / 1000.0
        )
        consent_active = (
            consent is not None and consent.revoked_at is None and not consent.paused
        )
        mapping_available = bool(state.session_workspaces) or bool(state.codex_session_bindings)
        signals = ObservationHealthSignals(
            consent_active=consent_active,
            mapping_available=mapping_available,
            source_coverage=coverage,
            pending_outbox_count=pending,
            lag_events=pending,
            gaps=tuple(sorted(state.gaps, key=str.encode)),
            unsupported_events=tuple(sorted(state.unsupported_events, key=str.encode)),
            advice_frontier=state.advice_frontier,
            last_hook_receipt_monotonic=last_hook,
            last_stream_advancement_monotonic=last_stream,
            last_successful_drain_monotonic=last_drain if pending == 0 else last_drain,
            session_ended=False,
        )
        lifecycle = compute_observation_lifecycle(
            signals,
            now_monotonic=self._now_mono(),
            thresholds=DEFAULT_OBSERVATION_HEALTH_THRESHOLDS,
        )
        return ObservationStatus(
            lifecycle=lifecycle,
            workspace_commitment=workspace_commitment,
            source_coverage=coverage,
            last_observation_receipt_time=state.last_receipt,
            lag_events=pending,
            gaps=tuple(sorted(state.gaps, key=str.encode)),
            unsupported_events=tuple(sorted(state.unsupported_events, key=str.encode)),
            advice_frontier=state.advice_frontier,
        )

    def _state_to_json(self, workspace: str, state: _WorkspaceState) -> dict[str, JsonValue]:
        consent = state.consent
        assert state.session_workspaces is not None
        assert state.cursors is not None
        assert state.dedup is not None
        assert state.envelopes is not None
        assert state.gaps is not None
        assert state.unsupported_events is not None
        assert state.open_pre is not None
        assert state.stream_cursors is not None
        assert state.codex_session_bindings is not None
        consent_json: JsonValue = None
        if consent is not None:
            consent_json = JsonObject(
                {
                    "workspace_commitment": consent.workspace_commitment,
                    "granted_at": consent.granted_at.wire,
                    "revoked_at": None if consent.revoked_at is None else consent.revoked_at.wire,
                    "paused": consent.paused,
                }
            )
        return {
            "schema": "yoetz.observation-local/1",
            "workspace_commitment": workspace,
            "consent": consent_json,
            "session_workspaces": JsonObject(
                {key: value for key, value in sorted(state.session_workspaces.items())}
            ),
            "cursors": JsonObject(
                {
                    key: observation_cursor_to_json(cursor)
                    for key, cursor in sorted(state.cursors.items())
                }
            ),
            "dedup": tuple(sorted(state.dedup, key=str.encode)),
            "envelopes": tuple(observation_envelope_to_json(item) for item in state.envelopes),
            "gaps": tuple(sorted(state.gaps, key=str.encode)),
            "unsupported_events": tuple(sorted(state.unsupported_events, key=str.encode)),
            "last_receipt": None if state.last_receipt is None else state.last_receipt.wire,
            "advice_frontier": state.advice_frontier,
            "advice_snapshot": (
                None
                if state.advice_snapshot is None
                else advice_snapshot_to_json(state.advice_snapshot)
            ),
            "last_advice_suppression": state.last_advice_suppression,
            "open_pre": JsonObject({key: value for key, value in sorted(state.open_pre.items())}),
            "stream_cursors": JsonObject(
                {
                    key: observation_cursor_to_json(cursor)
                    for key, cursor in sorted(state.stream_cursors.items())
                }
            ),
            "stream_partials": JsonObject(
                {
                    key: "b64:" + base64.b64encode(value).decode("ascii")
                    for key, value in sorted(
                        (state.stream_partials or {}).items(), key=lambda item: item[0].encode()
                    )
                }
            ),
            "hook_sequences": JsonObject(
                {
                    key: value
                    for key, value in sorted(
                        (state.hook_sequences or {}).items(), key=lambda item: item[0].encode()
                    )
                }
            ),
            "last_stream_reconcile_mono_ms": state.last_stream_reconcile_mono_ms,
            "last_hook_receipt_mono_ms": state.last_hook_receipt_mono_ms,
            "last_successful_drain_mono_ms": state.last_successful_drain_mono_ms,
            "pending_outbox": tuple(
                JsonObject(
                    {
                        "codex_session_id": session,
                        "envelope": observation_envelope_to_json(envelope),
                    }
                )
                for session, envelope in (state.pending_outbox or ())
            ),
            "codex_session_bindings": JsonObject(
                {key: value for key, value in sorted(state.codex_session_bindings.items())}
            ),
        }

    def _state_from_json(self, raw: Mapping[str, JsonValue]) -> _WorkspaceState:
        consent_raw = raw.get("consent")
        consent: LocalObservationConsent | None = None
        if isinstance(consent_raw, Mapping):
            row = cast(Mapping[str, JsonValue], consent_raw)
            revoked = row.get("revoked_at")
            consent = LocalObservationConsent(
                workspace_commitment=str(row["workspace_commitment"]),
                granted_at=Timestamp(str(row["granted_at"])),
                revoked_at=None if revoked is None else Timestamp(str(revoked)),
                paused=bool(row.get("paused", False)),
            )
        session_workspaces = {
            str(key): str(value)
            for key, value in cast(
                Mapping[str, JsonValue], raw.get("session_workspaces") or {}
            ).items()
        }
        cursors = {
            str(key): observation_cursor_from_json(JsonObject(cast(Mapping[str, JsonValue], value)))
            for key, value in cast(Mapping[str, JsonValue], raw.get("cursors") or {}).items()
        }
        dedup_raw = raw.get("dedup") or ()
        envelopes_raw = raw.get("envelopes") or ()
        gaps_raw = raw.get("gaps") or ()
        unsupported_raw = raw.get("unsupported_events") or ()
        advice_raw = raw.get("advice_snapshot")
        stream_cursors = {
            str(key): observation_cursor_from_json(JsonObject(cast(Mapping[str, JsonValue], value)))
            for key, value in cast(Mapping[str, JsonValue], raw.get("stream_cursors") or {}).items()
        }
        stream_partials: dict[str, bytes] = {}
        for key, value in cast(Mapping[str, JsonValue], raw.get("stream_partials") or {}).items():
            if type(value) is not str or not value.startswith("b64:"):
                continue
            try:
                stream_partials[str(key)] = base64.b64decode(
                    value[4:].encode("ascii"), validate=True
                )
            except (ValueError, OSError):
                continue
        hook_sequences: dict[str, int] = {}
        for key, value in cast(Mapping[str, JsonValue], raw.get("hook_sequences") or {}).items():
            if type(value) is int and not isinstance(value, bool) and value >= 0:
                hook_sequences[str(key)] = value
        reconcile_mono = raw.get("last_stream_reconcile_mono_ms")
        last_reconcile = (
            int(reconcile_mono)
            if type(reconcile_mono) is int and not isinstance(reconcile_mono, bool) and reconcile_mono >= 0
            else None
        )
        hook_mono_raw = raw.get("last_hook_receipt_mono_ms")
        last_hook_mono = (
            int(hook_mono_raw)
            if type(hook_mono_raw) is int
            and not isinstance(hook_mono_raw, bool)
            and hook_mono_raw >= 0
            else None
        )
        drain_mono_raw = raw.get("last_successful_drain_mono_ms")
        last_drain_mono = (
            int(drain_mono_raw)
            if type(drain_mono_raw) is int
            and not isinstance(drain_mono_raw, bool)
            and drain_mono_raw >= 0
            else None
        )
        bindings = {
            str(key): str(value)
            for key, value in cast(
                Mapping[str, JsonValue], raw.get("codex_session_bindings") or {}
            ).items()
        }
        open_pre = {
            str(key): str(value)
            for key, value in cast(Mapping[str, JsonValue], raw.get("open_pre") or {}).items()
        }
        last_receipt = raw.get("last_receipt")
        envelopes: list[ObservationEnvelope] = []
        for item in cast(tuple[JsonValue, ...] | list[JsonValue], envelopes_raw):
            if isinstance(item, Mapping):
                envelopes.append(
                    observation_envelope_from_json(JsonObject(cast(Mapping[str, JsonValue], item)))
                )
            else:
                envelopes.append(observation_envelope_from_json(item))
        advice_snapshot = None
        if advice_raw is not None:
            if isinstance(advice_raw, Mapping):
                advice_snapshot = advice_snapshot_from_json(
                    JsonObject(cast(Mapping[str, JsonValue], advice_raw))
                )
            else:
                advice_snapshot = advice_snapshot_from_json(advice_raw)
        pending_outbox: list[tuple[str, ObservationEnvelope]] = []
        for item in cast(tuple[JsonValue, ...] | list[JsonValue], raw.get("pending_outbox") or ()):
            if not isinstance(item, Mapping):
                continue
            row = cast(Mapping[str, JsonValue], item)
            session = row.get("codex_session_id")
            envelope_raw = row.get("envelope")
            if type(session) is not str or not isinstance(envelope_raw, Mapping):
                continue
            try:
                pending_outbox.append(
                    (
                        session,
                        observation_envelope_from_json(
                            JsonObject(cast(Mapping[str, JsonValue], envelope_raw))
                        ),
                    )
                )
            except (ProtocolValueError, TypeError, ValueError):
                continue
        return _WorkspaceState(
            consent=consent,
            session_workspaces=session_workspaces,
            cursors=cursors,
            dedup=set(cast(tuple[str, ...], dedup_raw)),
            envelopes=envelopes,
            gaps=set(cast(tuple[str, ...], gaps_raw)),
            unsupported_events=set(cast(tuple[str, ...], unsupported_raw)),
            last_receipt=None if last_receipt is None else Timestamp(str(last_receipt)),
            advice_frontier=cast(str | None, raw.get("advice_frontier")),
            advice_snapshot=advice_snapshot,
            last_advice_suppression=cast(str | None, raw.get("last_advice_suppression")),
            open_pre=open_pre,
            stream_cursors=stream_cursors,
            stream_partials=stream_partials,
            hook_sequences=hook_sequences,
            last_stream_reconcile_mono_ms=last_reconcile,
            last_hook_receipt_mono_ms=last_hook_mono,
            last_successful_drain_mono_ms=last_drain_mono,
            pending_outbox=pending_outbox,
            codex_session_bindings=bindings,
        )


def workspace_commitment_for_path(path: str, *, _state: Path | None = None) -> str:
    return LocalObservationStore(_state=_state).workspace_commitment(path)


def session_commitment_from_codex_id(key_material: bytes, codex_session_id: str) -> str:
    """Return a path-free session commitment for a Codex session token."""

    import hashlib
    import hmac

    if type(key_material) is not bytes or not 16 <= len(key_material) <= 64:
        raise ProtocolValueError("invalid_commitment")
    if type(codex_session_id) is not str or not codex_session_id or "\x00" in codex_session_id:
        raise ProtocolValueError("invalid_event_value_type")
    digest = hmac.new(
        key_material,
        _SESSION_DOMAIN + codex_session_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"hmac-sha256:{digest}"
