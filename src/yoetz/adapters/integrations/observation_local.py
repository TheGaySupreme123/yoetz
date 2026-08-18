"""Owner-private local observation consent, binding, and structural ingest state.

This store backs hook and ``yoetz observe`` controls when the service observation
handlers are unavailable. It retains allowlisted structure and commitments only —
never transcript prose or raw workspace paths.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import errno
import os
import re
import stat
import threading
import time
from collections.abc import Callable, Generator, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, cast

from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.domain.observation import (
    OBSERVATION_BACKPRESSURE_REASON,
    AdviceItem,
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
    observation_cursor_from_json,
    observation_cursor_to_json,
    observation_envelope_from_json,
    observation_envelope_to_json,
    workspace_commitment_from_path,
)
from yoetz.domain.values import (
    JsonObject,
    JsonValue,
    Timestamp,
    timestamp_from_datetime,
    validate_sha256_digest,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError

try:
    import fcntl
except ImportError:  # pragma: no cover - the Yoetz service is hosted on POSIX
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "HOOK_MAPPING_VERSION",
    "AdviceDelivery",
    "FrontierMotionNotice",
    "LocalObservationConsent",
    "LocalObservationStore",
    "ObservationOutboxRow",
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
_MAX_LEGACY_STATE_BYTES: Final = 36 * 1_048_576
_MAX_ENVELOPES: Final = 256
_MAX_DEDUP: Final = 4_096
_MAX_OPEN_PRE: Final = 256
_MAX_OUTBOX: Final = 512
_MAX_QUARANTINE: Final = 512
# Quarantined detail is a diagnostic aid, not the durable record; entries this
# stale are pure per-hook parse/encode tax (#211). Age-expired detail folds
# into the same aggregate eviction evidence as count/byte-cap evictions.
_MAX_QUARANTINE_AGE_DAYS: Final = 14
# A stream partial is a pure read-cache: the unterminated tail of the source
# JSONL, held so the next reconcile need not reread it. The reader seeks to
# the committed cursor and rereads the tail whenever the partial is absent,
# so dropping one costs a bounded reread, never observation loss. It is
# therefore bounded per entry and shed before any durable row when the state
# file approaches its cap (#289).
#
# The bound is the session reader's own read chunk
# (``codex_session_stream._MAX_READ_CHUNK``) and may not go below it. The
# reader assembles a source line across passes by holding its prefix here, so
# a bound smaller than one chunk makes any line longer than a chunk
# unassemblable: the hold is dropped, the next pass rereads the identical
# chunk, and the cursor never advances again for that session. Measured on a
# real Codex corpus, 648 of 1.54M rollout lines exceed 256 KiB, so the stall
# is reachable. Not imported, to keep this module free of a cycle with the
# reader that imports it; ``test_observation_state_bounds`` fences the drift.
_MAX_STREAM_PARTIAL_BYTES: Final = 262_144
# Parse-cache entry bound: hooks touch one workspace, the daemon's sweep loop
# touches all of them — without a cap the daemon would retain a parsed object
# graph per workspace forever.
_MAX_STATE_CACHE_ENTRIES: Final = 8
_MAX_HOOK_SEQUENCES: Final = 256
_MAX_FRONTIER_MOTION_NOTICES: Final = 256
_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
# Wall/monotonic drift tolerated before persisted monotonic samples are treated
# as belonging to a different boot epoch (and therefore fenced off).
_EPOCH_TOLERANCE_SECONDS: Final = 2.0
_OUTBOX_REASON_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_RUNTIME_GATE_SCHEMA: Final = "yoetz.observation-runtime-gate/1"
_RUNTIME_GATE_NAME: Final = "runtime-gate.json"
_MAX_RUNTIME_GATE_BYTES: Final = 256
# Never a legal character in an event-kind token. An interim build stamped
# hook timing after the kind as ``<kind>|<...>``; the reader below still trims
# it so such a value can never be mistaken for an event kind.
_OPEN_PRE_SEPARATOR: Final = "|"
_LOCAL_OUTBOX_OVERFLOW_GAP: Final = "_local_outbox_overflow"
_LOCAL_STREAM_PARTIAL_DROPPED_GAP: Final = "_local_stream_partial_dropped"
_LEGACY_STREAM_PARTIAL_DROPPED_SESSION: Final = "_legacy_unknown"
_STORE_LOCK_TIMEOUT_SECONDS: Final = 2.0
_STORE_LOCK_POLL_SECONDS: Final = 0.01
# Fraction of the state bound a save must leave free before an eviction gap is
# treated as healed (#310).
_STATE_HEADROOM_DIVISOR: Final = 8

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


class _StoreLockState:
    def __init__(self) -> None:
        self.thread_lock = threading.RLock()
        self.depth = 0
        self.descriptor: int | None = None


_STORE_LOCK_REGISTRY_GUARD = threading.Lock()
_STORE_LOCK_REGISTRY: dict[str, _StoreLockState] = {}


class _InterprocessStoreLock:
    """Reentrant process-local lock plus POSIX serialization across hook/daemon."""

    def __init__(self, path: Path, *, waits_ms: MutableMapping[str, float] | None = None) -> None:
        self._path = path
        # Queueing behind another process is real hook wall time that no stage
        # window could see: the wait lands inside and outside the timed 'store'
        # window alike, so it accumulates here, once per acquisition, wherever
        # in the pass it happens (#310/#311). Reentrant acquisitions return
        # immediately and contribute ~0.
        self._waits_ms = waits_ms
        key = str(path.absolute())
        with _STORE_LOCK_REGISTRY_GUARD:
            self._state = _STORE_LOCK_REGISTRY.setdefault(key, _StoreLockState())

    def __enter__(self) -> _InterprocessStoreLock:
        started = time.monotonic()
        try:
            return self._acquire(started + _STORE_LOCK_TIMEOUT_SECONDS)
        finally:
            if self._waits_ms is not None:
                # Recorded on the timeout path too: a pass that waited two
                # seconds and then failed spent those seconds queueing.
                self._waits_ms["lock_wait"] = (
                    self._waits_ms.get("lock_wait", 0.0) + (time.monotonic() - started) * 1000
                )

    def _acquire(self, deadline: float) -> _InterprocessStoreLock:
        state = self._state
        if not state.thread_lock.acquire(timeout=_STORE_LOCK_TIMEOUT_SECONDS):
            raise TimeoutError("observation_store_lock_timeout")
        if state.depth == 0:
            flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor: int | None = None
            try:
                descriptor = os.open(self._path, flags, 0o600)
                os.fchmod(descriptor, 0o600)
                if fcntl is not None:
                    while True:
                        try:
                            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except OSError as exc:
                            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                                raise
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError("observation_store_lock_timeout") from exc
                            time.sleep(min(_STORE_LOCK_POLL_SECONDS, remaining))
            except BaseException:
                if descriptor is not None:
                    os.close(descriptor)
                state.thread_lock.release()
                raise
            assert descriptor is not None
            state.descriptor = descriptor
        state.depth += 1
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        state = self._state
        try:
            state.depth -= 1
            if state.depth == 0:
                descriptor = state.descriptor
                state.descriptor = None
                if descriptor is not None:
                    try:
                        if fcntl is not None:
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                    finally:
                        os.close(descriptor)
        finally:
            state.thread_lock.release()


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
class AdviceDelivery:
    """One hook-channel advice delivery: the snapshot, the rendered item, its text."""

    snapshot: AdviceSnapshot
    item: AdviceItem | None
    delivery_identity: str
    text: str


@dataclass(frozen=True, slots=True)
class FrontierMotionNotice:
    """One bounded, one-shot notice that the observation writer moved a task frontier."""

    from_sequence: int
    to_sequence: int
    head_digest: str
    observation_record_count: int
    task_id: str

    def __post_init__(self) -> None:
        if (
            type(self.from_sequence) is not int
            or type(self.to_sequence) is not int
            or type(self.observation_record_count) is not int
            or type(self.task_id) is not str
            or not self.task_id
            or not 0 <= self.from_sequence < self.to_sequence <= _MAX_SAFE_INTEGER
            or not 1 <= self.observation_record_count <= self.to_sequence - self.from_sequence
        ):
            raise ProtocolValueError("invalid_event_value_type")
        validate_sha256_digest(self.head_digest)

    @property
    def delivery_identity(self) -> str:
        return canonical_digest(
            JsonObject(
                {
                    "from_sequence": self.from_sequence,
                    "head_digest": self.head_digest,
                    "observation_record_count": self.observation_record_count,
                    "task_id": self.task_id,
                    "to_sequence": self.to_sequence,
                }
            )
        )


def _clamp_frontier_motion_notice(
    notice: FrontierMotionNotice, delivered_to: int
) -> FrontierMotionNotice | None:
    """Drop or trim a candidate so it describes only motion past ``delivered_to``."""

    if notice.to_sequence <= delivered_to:
        return None
    if notice.from_sequence >= delivered_to:
        return notice
    remainder_span = notice.to_sequence - delivered_to
    original_span = notice.to_sequence - notice.from_sequence
    if notice.observation_record_count == original_span:
        remainder_count = remainder_span
    else:
        delivered_span = delivered_to - notice.from_sequence
        remainder_count = min(
            max(notice.observation_record_count - delivered_span, 1),
            remainder_span,
        )
    return FrontierMotionNotice(
        delivered_to,
        notice.to_sequence,
        notice.head_digest,
        remainder_count,
        notice.task_id,
    )


@dataclass(frozen=True, slots=True)
class LocalObservationConsent:
    workspace_commitment: str
    granted_at: Timestamp
    revoked_at: Timestamp | None = None
    paused: bool = False

    @property
    def active(self) -> bool:
        return self.revoked_at is None and not self.paused


@dataclass(frozen=True, slots=True)
class ObservationOutboxRow:
    """One bounded structural-delivery row; never contains plaintext content."""

    codex_session_id: str
    envelope: ObservationEnvelope
    attempts: int = 0
    last_reason: str | None = None
    last_attempt_at: Timestamp | None = None

    @property
    def row_identity(self) -> str:
        return canonical_digest(
            JsonObject(
                {
                    "codex_session_id": self.codex_session_id,
                    "envelope": observation_envelope_to_json(self.envelope),
                }
            )
        )

    def __post_init__(self) -> None:
        if type(self.codex_session_id) is not str or not self.codex_session_id:
            raise ProtocolValueError("invalid_event_value_type")
        if type(self.envelope) is not ObservationEnvelope:
            raise ProtocolValueError("invalid_event_value_type")
        if (
            type(self.attempts) is not int
            or isinstance(self.attempts, bool)
            or not 0 <= self.attempts <= _MAX_SAFE_INTEGER
        ):
            raise ProtocolValueError("invalid_event_value_type")
        if self.last_reason is not None and (
            type(self.last_reason) is not str
            or _OUTBOX_REASON_RE.fullmatch(self.last_reason) is None
        ):
            raise ProtocolValueError("invalid_event_value_type")
        if self.last_attempt_at is not None and type(self.last_attempt_at) is not Timestamp:
            raise ProtocolValueError("invalid_timestamp")


@dataclass
class _GapState:
    first_seen: Timestamp
    last_seen: Timestamp
    active: bool = True


@dataclass
class _WorkspaceState:
    consent: LocalObservationConsent | None = None
    session_workspaces: dict[str, str] | None = None
    cursors: dict[str, ObservationCursor] | None = None
    dedup: set[str] | None = None
    envelopes: list[ObservationEnvelope] | None = None
    gaps: dict[str, _GapState] | None = None
    unsupported_events: set[str] | None = None
    last_receipt: Timestamp | None = None
    advice_frontier: str | None = None
    advice_snapshot: AdviceSnapshot | None = None
    last_advice_suppression: str | None = None
    session_advice: dict[str, AdviceSnapshot] | None = None
    session_advice_suppression: dict[str, str] | None = None
    frontier_motion_notices: dict[str, FrontierMotionNotice] | None = None
    # Last delivered notice ``to_sequence`` per Codex session. Survives notice
    # deletion so a replayed append cannot re-announce already-delivered motion.
    frontier_motion_delivered: dict[str, tuple[str, int]] | None = None
    open_pre: dict[str, str] | None = None
    stream_cursors: dict[str, ObservationCursor] | None = None
    stream_partials: dict[str, bytes] | None = None
    stream_partial_dropped_sessions: set[str] | None = None
    hook_sequences: dict[str, int] | None = None
    last_stream_reconcile_mono_ms: int | None = None
    last_hook_receipt_mono_ms: int | None = None
    last_successful_drain_mono_ms: int | None = None
    # Boot/process epoch (wall - monotonic) the monotonic samples above belong
    # to. Samples are only comparable to a live clock within the same epoch;
    # after a restart or reboot they are fenced off (see `_epoch_matches`).
    monotonic_epoch: float | None = None
    pending_outbox: list[ObservationOutboxRow] | None = None
    # (codex_session_id, envelope, reason, quarantined_at). The timestamp is
    # store-authored at quarantine time so the age bound measures time *in*
    # quarantine, never the (possibly much older) envelope receipt time.
    quarantine: list[tuple[str, ObservationEnvelope, str, Timestamp]] | None = None
    codex_session_bindings: dict[str, str] | None = None
    storage_corrupt_sessions: set[str] | None = None
    ended_sessions: set[str] | None = None
    session_generations: dict[str, int] | None = None
    ended_session_generations: dict[str, int] | None = None
    quarantine_evicted_count: int = 0
    quarantine_reclaimed_count: int = 0
    quarantine_evicted_commitment: str | None = None
    quarantine_evicted_first: Timestamp | None = None
    quarantine_evicted_last: Timestamp | None = None
    trusted_policy_digest: str | None = None
    trusted_policy_mac: str | None = None

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
            self.gaps = {}
        if self.unsupported_events is None:
            self.unsupported_events = set()
        if self.open_pre is None:
            self.open_pre = {}
        if self.stream_cursors is None:
            self.stream_cursors = {}
        if self.stream_partials is None:
            self.stream_partials = {}
        if self.stream_partial_dropped_sessions is None:
            self.stream_partial_dropped_sessions = set()
        if self.hook_sequences is None:
            self.hook_sequences = {}
        if self.pending_outbox is None:
            self.pending_outbox = []
        if self.quarantine is None:
            self.quarantine = []
        if self.codex_session_bindings is None:
            self.codex_session_bindings = {}
        if self.storage_corrupt_sessions is None:
            self.storage_corrupt_sessions = set()
        if self.ended_sessions is None:
            self.ended_sessions = set()
        if self.session_generations is None:
            self.session_generations = {}
        if self.ended_session_generations is None:
            self.ended_session_generations = {}
        if self.session_advice is None:
            self.session_advice = {}
        if self.session_advice_suppression is None:
            self.session_advice_suppression = {}
        if self.frontier_motion_notices is None:
            self.frontier_motion_notices = {}
        if self.frontier_motion_delivered is None:
            self.frontier_motion_delivered = {}


def _cursor_key(source: ObservationSource, session_commitment: str) -> str:
    return f"{source.value}:{session_commitment}"


def _load_session_advice(raw: object) -> dict[str, AdviceSnapshot]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, AdviceSnapshot] = {}
    for key, value in cast(Mapping[str, JsonValue], raw).items():
        if type(key) is not str or not isinstance(value, Mapping):
            continue
        try:
            result[key] = advice_snapshot_from_json(
                JsonObject(cast(Mapping[str, JsonValue], value))
            )
        except ProtocolValueError, TypeError, ValueError:
            continue
    return result


def _load_frontier_motion_notices(raw: object) -> dict[str, FrontierMotionNotice]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, FrontierMotionNotice] = {}
    for key, value in cast(Mapping[str, JsonValue], raw).items():
        if type(key) is not str or not isinstance(value, Mapping):
            continue
        notice = cast(Mapping[str, JsonValue], value)
        try:
            result[key] = FrontierMotionNotice(
                cast(int, notice.get("from_sequence")),
                cast(int, notice.get("to_sequence")),
                cast(str, notice.get("head_digest")),
                cast(int, notice.get("observation_record_count")),
                cast(str, notice.get("task_id")),
            )
        except ProtocolValueError, TypeError, ValueError:
            continue
    return result


def _load_frontier_motion_delivered(raw: object) -> dict[str, tuple[str, int]]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, tuple[str, int]] = {}
    for key, value in cast(Mapping[str, JsonValue], raw).items():
        if type(key) is not str or not isinstance(value, Mapping):
            continue
        entry = cast(Mapping[str, JsonValue], value)
        task_id = entry.get("task_id")
        to_sequence = entry.get("to_sequence")
        if type(task_id) is not str or not task_id or type(to_sequence) is not int:
            continue
        if not 1 <= to_sequence <= _MAX_SAFE_INTEGER:
            continue
        result[key] = (task_id, to_sequence)
    return result


def _advice_delivery_scope_key(
    *,
    yoetz_session_id: str | None,
    session_commitment: str | None,
) -> str | None:
    """Prefer the mapped Yoetz session; otherwise the current Codex session."""

    if type(yoetz_session_id) is str:
        return yoetz_session_id
    if type(session_commitment) is str:
        return session_commitment
    return None


def _task_scoped_delivery_snapshot(
    state: _WorkspaceState,
    *,
    yoetz_session_id: str | None,
    session_commitment: str | None,
) -> AdviceSnapshot | None:
    """Task-scoped snapshot for hook delivery: never the workspace fallback.

    A mapped Yoetz session snapshot is authority when present. Otherwise the
    current Codex session's retained envelopes are rebuilt in memory so a
    same-session failed command stays deliverable before ``start`` returns.
    """

    if type(yoetz_session_id) is str and state.session_advice is not None:
        mapped = state.session_advice.get(yoetz_session_id)
        if mapped is not None:
            return mapped
    if type(session_commitment) is not str or not session_commitment:
        return None
    envelopes = tuple(
        envelope
        for envelope in (state.envelopes or ())
        if envelope.session_commitment == session_commitment
    )
    if not envelopes:
        return None
    from yoetz.application.observation_advice import (
        ObservationAdviceBuildInput,
        build_observation_advice_snapshot,
    )

    return build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            has_real_observation=True,
        )
    )


def _copy_state(state: _WorkspaceState) -> _WorkspaceState:
    """Independent copy of one workspace state for the parse cache.

    Containers are copied; contained values (envelopes, rows, cursors,
    snapshots, timestamps) are frozen dataclasses or immutable builtins, so a
    shallow container copy fully isolates callers from the cached instance.
    """

    return dataclasses.replace(
        state,
        session_workspaces=dict(state.session_workspaces or {}),
        cursors=dict(state.cursors or {}),
        dedup=set(state.dedup or ()),
        envelopes=list(state.envelopes or ()),
        gaps=dict(state.gaps or {}),
        unsupported_events=set(state.unsupported_events or ()),
        session_advice=dict(state.session_advice or {}),
        session_advice_suppression=dict(state.session_advice_suppression or {}),
        frontier_motion_notices=dict(state.frontier_motion_notices or {}),
        frontier_motion_delivered=dict(state.frontier_motion_delivered or {}),
        open_pre=dict(state.open_pre or {}),
        stream_cursors=dict(state.stream_cursors or {}),
        stream_partials=dict(state.stream_partials or {}),
        stream_partial_dropped_sessions=set(state.stream_partial_dropped_sessions or ()),
        hook_sequences=dict(state.hook_sequences or {}),
        pending_outbox=list(state.pending_outbox or ()),
        quarantine=list(state.quarantine or ()),
        codex_session_bindings=dict(state.codex_session_bindings or {}),
        storage_corrupt_sessions=set(state.storage_corrupt_sessions or ()),
        ended_sessions=set(state.ended_sessions or ()),
        session_generations=dict(state.session_generations or {}),
        ended_session_generations=dict(state.ended_session_generations or {}),
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
        _wall: Callable[[], float] | None = None,
    ) -> None:
        self._root = observation_dir(_state=_state)
        self._state_root = self._root.parent
        # Monotonic milliseconds accumulated per store sub-stage since
        # construction. Hooks fold these into their pass-timing rows so the
        # formerly opaque 'store' stage is attributable (#290), and so time
        # spent queueing for the store lock is reported as queueing rather
        # than as unexplained wall time (#310).
        self.stage_timings_ms: dict[str, float] = {
            "hydrate": 0.0,
            "encode": 0.0,
            "lock_wait": 0.0,
            "write": 0.0,
        }
        self._lock = _InterprocessStoreLock(
            self._root / ".store.lock", waits_ms=self.stage_timings_ms
        )
        self._monotonic = _monotonic
        self._wall = _wall
        # Parse cache keyed by workspace commitment, validated by the state
        # file's (inode, size, mtime_ns, ctime_ns). Hooks are one-shot
        # processes that call many store methods against the same file;
        # re-reading and re-parsing a ~400KB state on every method call
        # dominated hook wall time (#209). _atomic_write replaces the inode,
        # so a stat match means the cached parse is byte-current even across
        # processes. Bounded so the long-lived daemon, which iterates every
        # workspace on its sweep loop, never accretes one parsed object graph
        # per workspace it has ever seen.
        self._state_cache: dict[str, tuple[tuple[int, int, int, int], _WorkspaceState]] = {}
        self._key_material_cache: bytes | None = None
        # Open write batches keyed by workspace commitment. Inside a batch
        # `_load` hands back the held mutable state and `_save` only marks it
        # dirty, so one hook pass serializes and fsyncs once instead of the
        # 10-18 times measured on a lived-in store (#242).
        self._batch: dict[str, _WorkspaceState] = {}
        self._batch_dirty: set[str] = set()

    def _now_mono(self) -> float:
        import time

        return time.monotonic() if self._monotonic is None else self._monotonic()

    def _wall_now(self) -> float:
        import time

        return time.time() if self._wall is None else self._wall()

    def _wall_timestamp(self) -> Timestamp:
        current = datetime.fromtimestamp(self._wall_now(), UTC)
        stamp = current.replace(microsecond=(current.microsecond // 1000) * 1000)
        return timestamp_from_datetime(stamp)

    def _boot_epoch(self) -> float:
        """Approximate wall time at monotonic zero: stable within a boot session.

        A reboot resets the monotonic clock, so this shifts by the previous
        uptime and cleanly distinguishes samples from an earlier boot.
        """

        return self._wall_now() - self._now_mono()

    def _epoch_matches(self, epoch: float | None) -> bool:
        return epoch is not None and abs(self._boot_epoch() - epoch) <= _EPOCH_TOLERANCE_SECONDS

    def key_material(self) -> bytes:
        with self._lock:
            path = self._root / "key-material.bin"
            existing = _read_bytes(path, maximum=_KEY_BYTES)
            if existing is not None and len(existing) == _KEY_BYTES:
                return existing
            material = os.urandom(_KEY_BYTES)
            _atomic_write(path, material)
            return material

    def _cached_key_material(self) -> bytes:
        # The key file is created once and never rewritten, so a per-instance
        # memo is safe; the uncached read costs a file open per call, which
        # multiplies badly inside per-entry loops (quarantine eviction).
        if self._key_material_cache is None:
            self._key_material_cache = self.key_material()
        return self._key_material_cache

    def set_runtime_enabled(self, enabled: bool) -> None:
        """Publish the service-loaded observation gate for config-free hook reads.

        The marker is synchronized when a fresh READY composition is built.  A
        missing marker preserves the typed configuration default (enabled);
        malformed or unsafe markers fail closed in :meth:`runtime_enabled`.
        """

        if type(enabled) is not bool:
            raise TypeError("observation_runtime_gate_invalid")
        payload = (
            canonical_encode(JsonObject({"schema": _RUNTIME_GATE_SCHEMA, "enabled": enabled}))
            + b"\n"
        )
        with self._lock:
            _atomic_write(self._root / _RUNTIME_GATE_NAME, payload)

    def runtime_enabled(self) -> bool:
        """Return the current service-synchronized capture gate, failing closed.

        Deliberately lock-free: the marker is a tiny owner-only file that
        :meth:`set_runtime_enabled` only ever replaces atomically, and every
        hook event begins with this read. Serializing it on the interprocess
        store lock converted ordinary batch contention into gate failures
        that discarded events (#273). Reading through one descriptor keeps
        the safety checks and the payload bound to a single inode instead.
        """

        path = self._root / _RUNTIME_GATE_NAME
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return True
        except OSError as exc:
            message = (
                "Observation runtime gate is unsafe."
                if exc.errno == errno.ELOOP
                else "Observation runtime gate is unavailable."
            )
            raise _error(PublicErrorCode.STORAGE_UNSAFE, message, retryable=False) from exc
        try:
            facts = os.fstat(descriptor)
            if (
                not stat.S_ISREG(facts.st_mode)
                or facts.st_uid != os.geteuid()
                or facts.st_mode & 0o077
                or facts.st_size <= 0
                or facts.st_size > _MAX_RUNTIME_GATE_BYTES
            ):
                raise _error(
                    PublicErrorCode.STORAGE_UNSAFE,
                    "Observation runtime gate is unsafe.",
                    retryable=False,
                )
            chunks: list[bytes] = []
            remaining = _MAX_RUNTIME_GATE_BYTES + 1
            while remaining > 0 and (chunk := os.read(descriptor, remaining)):
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > _MAX_RUNTIME_GATE_BYTES:
                raise _error(
                    PublicErrorCode.STORAGE_UNSAFE,
                    "Observation runtime gate is unsafe.",
                    retryable=False,
                )
        except OSError as exc:
            raise _error(
                PublicErrorCode.STORAGE_UNSAFE,
                "Observation runtime gate is invalid.",
                retryable=False,
            ) from exc
        finally:
            os.close(descriptor)
        try:
            parsed = strict_json_parse(raw)
        except ProtocolValueError as exc:
            raise _error(
                PublicErrorCode.STORAGE_UNSAFE,
                "Observation runtime gate is invalid.",
                retryable=False,
            ) from exc
        if (
            not isinstance(parsed, Mapping)
            or set(parsed) != {"schema", "enabled"}
            or parsed.get("schema") != _RUNTIME_GATE_SCHEMA
            or type(parsed.get("enabled")) is not bool
        ):
            raise _error(
                PublicErrorCode.STORAGE_UNSAFE,
                "Observation runtime gate is invalid.",
                retryable=False,
            )
        return cast(bool, parsed["enabled"])

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

    def begin_session_generation(self, workspace_commitment: str, session_commitment: str) -> int:
        """Advance the durable generation and clear only the prior stopped fence."""

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.session_generations is not None
            assert state.ended_session_generations is not None
            assert state.ended_sessions is not None
            generation = state.session_generations.get(session_commitment, 0) + 1
            state.session_generations[session_commitment] = generation
            state.ended_session_generations.pop(session_commitment, None)
            state.ended_sessions.discard(session_commitment)
            assert state.stream_partial_dropped_sessions is not None
            state.stream_partial_dropped_sessions.discard(session_commitment)
            state.stream_partial_dropped_sessions.discard(_LEGACY_STREAM_PARTIAL_DROPPED_SESSION)
            if not state.stream_partial_dropped_sessions:
                self._resolve_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
            self._save(workspace_commitment, state)
            return generation

    def current_session_generation(self, workspace_commitment: str, session_commitment: str) -> int:
        with self._lock:
            state = self._load(workspace_commitment)
            assert state.session_generations is not None
            return state.session_generations.get(session_commitment, 1)

    def note_session_end(
        self,
        workspace_commitment: str,
        session_commitment: str,
        *,
        generation: int | None = None,
    ) -> None:
        """Persist that a bound Codex session ended.

        When every bound session for a workspace has ended (or consent stops),
        the lifecycle reports STOPPED rather than lingering as DEGRADED.
        """

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.ended_sessions is not None
            assert state.session_generations is not None
            assert state.ended_session_generations is not None
            assert state.session_workspaces is not None
            current = state.session_generations.get(session_commitment, 1)
            observed = current if generation is None else generation
            if observed != current:
                return
            # Retain the binding so "all bound sessions ended" is computable.
            state.session_workspaces.setdefault(session_commitment, workspace_commitment)
            state.ended_sessions.add(session_commitment)
            state.ended_session_generations[session_commitment] = observed
            assert state.stream_partial_dropped_sessions is not None
            state.stream_partial_dropped_sessions.discard(session_commitment)
            if not state.stream_partial_dropped_sessions:
                self._resolve_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
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

    def codex_session_ended(self, workspace_commitment: str, codex_session_id: str) -> bool:
        """Whether the bound Codex session is marked ended for its current generation.

        Drain routing consults this for ``mapping_missing`` rejections: pending outbox rows
        of a session that ended while unmapped can never deliver -- no future ``start`` will
        map an ended session -- so they get a terminal state instead of retrying forever and
        holding outbox capacity (#275). A restarted session clears the mark via
        ``begin_session_generation``.
        """

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.codex_session_bindings is not None
            assert state.ended_sessions is not None
            session = state.codex_session_bindings.get(codex_session_id)
            return session is not None and session in state.ended_sessions

    def quarantine_ended_unmapped_session(
        self,
        workspace: str,
        codex_session_id: str,
        reason: str,
    ) -> int:
        """Quarantine an ended unmapped lane only when no attach owns its lifecycle lock.

        A concurrent turn-boundary attach must win over terminalization: it may have
        received mapping_missing before persisting the mapping. Acquiring the same
        lifecycle lock closes that race; a contended lock leaves every row pending for
        the next pass, while an uncontended ended session is terminal (#275).
        """

        if not self.codex_session_ended(workspace, codex_session_id):
            return 0
        from yoetz.adapters.integrations.codex_lifecycle import acquire_session_lock

        with acquire_session_lock(codex_session_id, _state=self._state_root) as owned:
            if not owned or not self.codex_session_ended(workspace, codex_session_id):
                return 0
            return self.quarantine_outbox_session(workspace, codex_session_id, reason)

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

    def codex_sessions_for_workspace(self, workspace_commitment: str) -> tuple[str, ...]:
        """Return the bounded structural session IDs already bound to one workspace."""

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.codex_session_bindings is not None
            return tuple(sorted(state.codex_session_bindings, key=str.encode))

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
        """Record an open Pre event awaiting its Post."""

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
            raw = state.open_pre.pop(correlation_id, None)
            if raw is None:
                return None
            # A post consuming its open pre is live proof pairing works now;
            # a latched unpaired_event no longer describes this workspace (#274).
            self._resolve_gap_state(state, ObservationGapCode.UNPAIRED_EVENT.value)
            self._save(workspace, state)
            return raw.split(_OPEN_PRE_SEPARATOR, 1)[0]

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

    def set_session_advice_snapshot(
        self,
        workspace: str,
        *,
        yoetz_session_id: str,
        snapshot: AdviceSnapshot | None,
    ) -> None:
        with self._lock:
            state = self._load(workspace)
            if state.session_advice is None:
                state.session_advice = {}
            if snapshot is None:
                state.session_advice.pop(yoetz_session_id, None)
            else:
                state.session_advice[yoetz_session_id] = snapshot
            self._save(workspace, state)

    def peek_advice_for_delivery(
        self,
        workspace: str,
        *,
        yoetz_session_id: str | None = None,
        allow_standing: bool = True,
        session_commitment: str | None = None,
    ) -> AdviceDelivery | None:
        """Select advice to deliver once per *condition* identity. Never mutates state.

        Pure read by construction: the caller records the delivery with
        ``commit_advice_delivery`` only after the text has actually reached the
        agent. Persisting the identity here would mean a hook killed between
        the save and the stdout write suppresses that advice permanently, which
        is strictly worse than the storm this cadence exists to stop.

        Task-scoped conditions never fall back to the workspace-wide snapshot
        (#249). A mapped Yoetz session uses its own snapshot when present;
        otherwise selection is rebuilt from the current Codex session's
        retained envelopes. The workspace snapshot may contribute only
        deliberately standing machine conditions.

        ``allow_standing=False`` withholds standing machine conditions
        (STANDING_MACHINE_ACTIONS) and falls through to the highest-ranked
        actionable item, so those conditions reach the agent only on the
        session-boundary events that opt in (#241).
        """

        from yoetz.application.observation_advice import (
            advice_delivery_identity,
            hook_advice_context,
            select_advice_item,
            select_standing_item,
        )

        with self._lock:
            state = self._load(workspace)
            snapshot = _task_scoped_delivery_snapshot(
                state,
                yoetz_session_id=yoetz_session_id,
                session_commitment=session_commitment,
            )
            item = None
            if snapshot is not None:
                if not snapshot.ranked_finding_ids:
                    snapshot = None
                else:
                    item = select_advice_item(snapshot, allow_standing=allow_standing)
                    if item is None and snapshot.ranked_items:
                        # Every ranked item on the task-scoped snapshot was
                        # cadence-gated on this event class.
                        snapshot = None
            if snapshot is None and allow_standing and state.advice_snapshot is not None:
                standing = select_standing_item(state.advice_snapshot)
                if standing is not None:
                    snapshot = state.advice_snapshot
                    item = standing
            if snapshot is None:
                return None
            identity = advice_delivery_identity(snapshot, item=item)
            scope = _advice_delivery_scope_key(
                yoetz_session_id=yoetz_session_id,
                session_commitment=session_commitment,
            )
            if type(scope) is str:
                delivered = (state.session_advice_suppression or {}).get(scope)
            else:
                delivered = state.last_advice_suppression
            if delivered == identity:
                return None
            return AdviceDelivery(
                snapshot=snapshot,
                item=item,
                delivery_identity=identity,
                text=hook_advice_context(snapshot, item=item),
            )

    def commit_advice_delivery(
        self,
        workspace: str,
        delivery_identity: str,
        *,
        yoetz_session_id: str | None = None,
        session_commitment: str | None = None,
    ) -> None:
        """Record advice as delivered — call only after the text reached the agent.

        ``last_advice_suppression`` / ``session_advice_suppression`` hold
        ``deliver-`` tokens, not the snapshot's ``suppress-`` identity. Both
        persist as unvalidated ``str | None``, so a pre-upgrade file simply
        never matches and the first hook after upgrade delivers once — no
        migration, and downgrade is symmetric. They stay single values, never a
        set: A→B→A must redeliver A.

        Mapped deliveries key suppression by Yoetz session; unmapped hook
        deliveries key it by Codex session commitment so one task cannot
        silence another (#249). Callers that pass neither still use the
        workspace-wide slot.

        A crash between the emission and this call costs at most one
        redelivery; the reverse order would cost the advice entirely.
        """

        if type(delivery_identity) is not str or not delivery_identity:
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            scope = _advice_delivery_scope_key(
                yoetz_session_id=yoetz_session_id,
                session_commitment=session_commitment,
            )
            if type(scope) is str:
                if state.session_advice_suppression is None:
                    state.session_advice_suppression = {}
                if state.session_advice_suppression.get(scope) == delivery_identity:
                    return
                state.session_advice_suppression[scope] = delivery_identity
            else:
                if state.last_advice_suppression == delivery_identity:
                    return
                state.last_advice_suppression = delivery_identity
            self._save(workspace, state)

    def note_frontier_motion(
        self,
        workspace: str,
        codex_session_id: str,
        *,
        from_sequence: int,
        to_sequence: int,
        head_digest: str,
        observation_record_count: int,
        task_id: str,
    ) -> None:
        """Merge contiguous undelivered observation appends into one pending notice."""

        candidate = FrontierMotionNotice(
            from_sequence,
            to_sequence,
            head_digest,
            observation_record_count,
            task_id,
        )
        with self._lock:
            state = self._load(workspace)
            assert state.frontier_motion_notices is not None
            assert state.frontier_motion_delivered is not None
            notices = state.frontier_motion_notices
            delivered = state.frontier_motion_delivered
            mutated = False
            # The delivered mark is scoped to the task ledger it was announced
            # for. A session whose mapping moves to another task (or whose
            # prior ledger was announced under a different task id) must not
            # have the new ledger's motion suppressed by the old mark.
            delivered_entry = delivered.get(codex_session_id)
            if delivered_entry is not None and delivered_entry[0] != task_id:
                del delivered[codex_session_id]
                delivered_entry = None
                mutated = True
            delivered_to = delivered_entry[1] if delivered_entry is not None else 0
            prior = notices.get(codex_session_id)
            if prior is not None and prior.task_id != task_id:
                del notices[codex_session_id]
                prior = None
                mutated = True
            if prior is not None and prior.to_sequence <= delivered_to:
                del notices[codex_session_id]
                prior = None
                mutated = True
            if candidate.to_sequence <= delivered_to:
                if mutated:
                    self._save(workspace, state)
                return
            if prior is not None and prior.to_sequence == candidate.from_sequence:
                candidate = FrontierMotionNotice(
                    prior.from_sequence,
                    candidate.to_sequence,
                    candidate.head_digest,
                    prior.observation_record_count + candidate.observation_record_count,
                    candidate.task_id,
                )
            elif prior is not None and candidate.to_sequence <= prior.to_sequence:
                if mutated:
                    self._save(workspace, state)
                return
            clamped = _clamp_frontier_motion_notice(candidate, delivered_to)
            if clamped is None:
                notices.pop(codex_session_id, None)
                self._save(workspace, state)
                return
            notices[codex_session_id] = clamped
            self._save(workspace, state)

    def peek_frontier_motion(
        self, workspace: str, codex_session_id: str
    ) -> FrontierMotionNotice | None:
        with self._lock:
            state = self._load(workspace)
            return (state.frontier_motion_notices or {}).get(codex_session_id)

    def commit_frontier_motion_delivery(
        self, workspace: str, codex_session_id: str, delivery_identity: str
    ) -> None:
        """Remove exactly the notice whose context bytes reached the hook consumer."""

        with self._lock:
            state = self._load(workspace)
            notices = state.frontier_motion_notices or {}
            current = notices.get(codex_session_id)
            if current is None or current.delivery_identity != delivery_identity:
                return
            del notices[codex_session_id]
            assert state.frontier_motion_delivered is not None
            delivered = state.frontier_motion_delivered
            previous = delivered.pop(codex_session_id, None)
            previous_to = (
                previous[1] if previous is not None and previous[0] == current.task_id else 0
            )
            delivered[codex_session_id] = (
                current.task_id,
                max(previous_to, current.to_sequence),
            )
            self._save(workspace, state)

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
                    gaps=status.gaps,
                    check_facts=typed_checks,
                    inspect_fact=typed_inspect,
                    composition=typed_composition,
                    plan_path_digests=typed_plans,
                    prior_snapshot=state.advice_snapshot,
                    semantic_addon=typed_semantic,
                    has_real_observation=bool(state.envelopes),
                )
            )
            if snapshot is not state.advice_snapshot:
                # build_observation_advice_snapshot returns the prior object
                # unchanged when nothing moved; rewriting it cost ~91 ms of
                # encode plus an fsync on every suppressed hook (#242). The
                # pruning `_save` drives still runs: a hook flushes its batch
                # exactly once regardless of this branch.
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

    def set_stream_partial(self, workspace: str, session_commitment: str, partial: bytes) -> None:
        if type(partial) is not bytes:
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            assert state.stream_partials is not None
            assert state.stream_partial_dropped_sessions is not None
            if len(partial) > _MAX_STREAM_PARTIAL_BYTES:
                # An oversized tail drops with an explicit gap instead of
                # pinning megabytes of read-cache in the state file: the
                # reader rereads it from the committed cursor on the next
                # reconcile (#289). Raising here instead stalled the stream
                # forever while retaining the partial that caused the stall.
                state.stream_partials.pop(session_commitment, None)
                state.stream_partial_dropped_sessions.add(session_commitment)
                self._note_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
            elif partial:
                state.stream_partials[session_commitment] = partial
                state.stream_partial_dropped_sessions.discard(session_commitment)
            else:
                state.stream_partials.pop(session_commitment, None)
                state.stream_partial_dropped_sessions.discard(session_commitment)
            if not state.stream_partial_dropped_sessions:
                self._resolve_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
            self._save(workspace, state)

    def note_stream_reconcile(self, workspace: str, *, mono: float | None = None) -> None:
        import time

        with self._lock:
            state = self._load(workspace)
            current = time.monotonic() if mono is None else mono
            state.last_stream_reconcile_mono_ms = int(current * 1000)
            state.monotonic_epoch = self._boot_epoch()
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
                self._note_gap_state(state, _LOCAL_OUTBOX_OVERFLOW_GAP)
                self._save(workspace, state)
                return ObservationGapCode.OUTBOX_OVERFLOW.value
            # Dedup identical source identities already pending for this session.
            for row in state.pending_outbox:
                if (
                    row.codex_session_id == codex_session_id
                    and row.envelope.source_identity == envelope.source_identity
                    and row.envelope.event_kind == envelope.event_kind
                    and row.envelope.cursor.event_position == envelope.cursor.event_position
                ):
                    return None
            state.pending_outbox.append(
                ObservationOutboxRow(codex_session_id=codex_session_id, envelope=envelope)
            )
            # Resolve before projecting so the size-checked bytes are exactly
            # the bytes _save would otherwise re-encode: one encode, not three.
            self._resolve_gap_state(state, _LOCAL_OUTBOX_OVERFLOW_GAP)
            projected = self._encode_state(workspace, state)
            if len(projected) > _MAX_STATE_BYTES:
                state.pending_outbox.pop()
                self._note_gap_state(state, _LOCAL_OUTBOX_OVERFLOW_GAP)
                self._save(workspace, state)
                return ObservationGapCode.OUTBOX_OVERFLOW.value
            self._save(workspace, state, projected=projected)
            return None

    def list_pending_outbox(
        self, workspace: str, *, codex_session_id: str | None = None
    ) -> tuple[tuple[str, ObservationEnvelope], ...]:
        """Return the legacy two-field view used by existing hook/setup callers."""

        rows = self.list_pending_outbox_rows(workspace, codex_session_id=codex_session_id)
        return tuple((row.codex_session_id, row.envelope) for row in rows)

    def list_pending_outbox_rows(
        self, workspace: str, *, codex_session_id: str | None = None
    ) -> tuple[ObservationOutboxRow, ...]:
        """Return immutable pending rows including bounded delivery-attempt metadata."""

        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            if codex_session_id is None:
                return tuple(state.pending_outbox)
            return tuple(
                row for row in state.pending_outbox if row.codex_session_id == codex_session_id
            )

    def pending_workspaces(self) -> tuple[str, ...]:
        """Return only opaque commitments for workspaces with undelivered rows."""

        with self._lock:
            pending: list[str] = []
            for workspace, state in self._iter_workspaces():
                assert state.pending_outbox is not None
                if state.pending_outbox:
                    pending.append(workspace)
            return tuple(sorted(pending, key=str.encode))

    def bump_outbox_row_attempt(
        self,
        workspace: str,
        expected: ObservationOutboxRow,
        *,
        reason: str | None,
        attempted_at: Timestamp | None = None,
    ) -> ObservationOutboxRow | None:
        """Persist one exact delivery attempt and return its new durable value."""

        if reason is not None and (
            type(reason) is not str or _OUTBOX_REASON_RE.fullmatch(reason) is None
        ):
            raise ProtocolValueError("invalid_event_value_type")
        stamp = self._wall_timestamp() if attempted_at is None else attempted_at
        if type(stamp) is not Timestamp:
            raise ProtocolValueError("invalid_timestamp")
        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            for index, row in enumerate(state.pending_outbox):
                if row.row_identity == expected.row_identity and row.attempts == expected.attempts:
                    updated = ObservationOutboxRow(
                        codex_session_id=row.codex_session_id,
                        envelope=row.envelope,
                        attempts=min(row.attempts + 1, _MAX_SAFE_INTEGER),
                        last_reason=reason,
                        last_attempt_at=stamp,
                    )
                    state.pending_outbox[index] = updated
                    self._save(workspace, state)
                    return updated
            return None

    def pending_outbox_count(self, workspace: str) -> int:
        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            return len(state.pending_outbox)

    def last_successful_drain_mono(self, workspace: str) -> float | None:
        """Return the current-boot monotonic drain sample, if one is comparable."""

        with self._lock:
            state = self._load(workspace)
            if not self._epoch_matches(state.monotonic_epoch):
                return None
            value = state.last_successful_drain_mono_ms
            return None if value is None else value / 1000.0

    def note_outbox_session_reason(self, workspace: str, codex_session_id: str, reason: str) -> int:
        """Stamp a shared last_reason on every un-reasoned pending row of one session.

        Used when a drain pass retires a session after one probe (its rows all
        fail identically): the probed row carries the reason from its real
        attempt, and this stamps the skipped siblings in a single save so
        ``observe status`` reports the true shared cause instead of
        ``not_attempted``. Attempt counts are untouched — no attempt was made.
        """

        if _OUTBOX_REASON_RE.fullmatch(reason) is None:
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            stamped = 0
            for index, row in enumerate(state.pending_outbox):
                if row.codex_session_id == codex_session_id and row.last_reason is None:
                    state.pending_outbox[index] = dataclasses.replace(row, last_reason=reason)
                    stamped += 1
            if stamped:
                self._save(workspace, state)
            return stamped

    @contextlib.contextmanager
    def drain_lease(self, workspace: str) -> Generator[bool]:
        """Nonblocking per-workspace drain mutex; yields whether it was acquired.

        Codex runs async hooks concurrently (up to 8), and every hook drains
        the same workspace outbox. Without a lease each concurrent hook
        re-ingests the identical backlog — 8x daemon load for zero additional
        delivery. Losing the lease is not a failure: another live hook process
        is already draining.
        """

        digest = workspace.removeprefix("hmac-sha256:")
        if len(digest) != 64:
            raise ProtocolValueError("invalid_commitment")
        path = self._root / f".drain-{digest}.lock"
        if fcntl is None:  # pragma: no cover - POSIX-only host
            yield True
            return
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    @contextlib.contextmanager
    def advice_delivery_lease(self, workspace: str) -> Generator[bool]:
        """Nonblocking advice-delivery mutex independent of workspace state.

        The lease serializes selection, stdout emission, and the subsequent
        delivery commit across concurrent hook processes.  It deliberately
        uses a separate lock file: a blocked host stdout pipe may delay another
        advice delivery, but must never block observation ingest or outbox work
        that needs the workspace-state lock.
        """

        digest = workspace.removeprefix("hmac-sha256:")
        if len(digest) != 64:
            raise ProtocolValueError("invalid_commitment")
        path = self._root / f".advice-delivery-{digest}.lock"
        if fcntl is None:  # pragma: no cover - POSIX-only host
            yield True
            return
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def acknowledge_outbox(
        self, workspace: str, codex_session_id: str, source_identity: str
    ) -> bool:
        """Remove one outbox entry after the task-bundle transaction has committed."""

        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            for index, row in enumerate(state.pending_outbox):
                if (
                    row.codex_session_id == codex_session_id
                    and row.envelope.source_identity == source_identity
                ):
                    del state.pending_outbox[index]
                    self._resolve_delivered(state)
                    state.last_successful_drain_mono_ms = int(self._now_mono() * 1000)
                    state.monotonic_epoch = self._boot_epoch()
                    self._save(workspace, state)
                    return True
            return False

    def acknowledge_outbox_row(self, workspace: str, expected: ObservationOutboxRow) -> bool:
        """Acknowledge only an exact attempted row, never a same-source successor."""

        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            for index, row in enumerate(state.pending_outbox):
                if row.row_identity == expected.row_identity and row.attempts == expected.attempts:
                    del state.pending_outbox[index]
                    self._resolve_delivered(state)
                    state.last_successful_drain_mono_ms = int(self._now_mono() * 1000)
                    state.monotonic_epoch = self._boot_epoch()
                    self._save(workspace, state)
                    return True
            return False

    def quarantine_outbox(
        self, workspace: str, codex_session_id: str, source_identity: str, reason: str
    ) -> bool:
        """Move a permanently-rejected outbox entry into a bounded, visible quarantine.

        Quarantined entries are never treated as committed: they leave the pending
        drain queue but are retained and surface as an ``outbox_quarantined``
        coverage gap in status until an operator reclaims them or the count,
        byte-budget, or clock-fenced age bound folds them into the aggregate
        eviction evidence — never a silent drop.
        """

        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            assert state.quarantine is not None
            assert state.gaps is not None
            moved: ObservationEnvelope | None = None
            for index, row in enumerate(state.pending_outbox):
                if (
                    row.codex_session_id == codex_session_id
                    and row.envelope.source_identity == source_identity
                ):
                    moved = row.envelope
                    del state.pending_outbox[index]
                    break
            if moved is None:
                return False
            already = any(
                entry[0] == codex_session_id and entry[1].source_identity == source_identity
                for entry in state.quarantine
            )
            if not already:
                state.quarantine.append((codex_session_id, moved, reason, self._wall_timestamp()))
                # Bounded detail with permanent aggregate evidence for evictions.
                while len(state.quarantine) > _MAX_QUARANTINE:
                    evicted = state.quarantine.pop(0)
                    self._record_quarantine_eviction(state, evicted[0], evicted[1], evicted[2])
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            self._save(workspace, state)
            return True

    def quarantine_outbox_row(
        self, workspace: str, expected: ObservationOutboxRow, reason: str
    ) -> bool:
        """Quarantine only the exact attempted row selected by a drain actor."""

        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            assert state.quarantine is not None
            assert state.gaps is not None
            moved: ObservationEnvelope | None = None
            for index, row in enumerate(state.pending_outbox):
                if row.row_identity == expected.row_identity and row.attempts == expected.attempts:
                    moved = row.envelope
                    del state.pending_outbox[index]
                    break
            if moved is None:
                return False
            already = any(
                entry[0] == expected.codex_session_id
                and entry[1].source_identity == moved.source_identity
                and observation_envelope_to_json(entry[1]) == observation_envelope_to_json(moved)
                for entry in state.quarantine
            )
            if not already:
                state.quarantine.append(
                    (expected.codex_session_id, moved, reason, self._wall_timestamp())
                )
                while len(state.quarantine) > _MAX_QUARANTINE:
                    evicted = state.quarantine.pop(0)
                    self._record_quarantine_eviction(state, evicted[0], evicted[1], evicted[2])
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            self._save(workspace, state)
            return True

    def quarantine_outbox_session(self, workspace: str, codex_session_id: str, reason: str) -> int:
        """Atomically quarantine every pending row for one terminally failed session."""

        if type(codex_session_id) is not str or not codex_session_id:
            raise ProtocolValueError("invalid_event_value_type")
        if type(reason) is not str or _OUTBOX_REASON_RE.fullmatch(reason) is None:
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            assert state.pending_outbox is not None
            assert state.quarantine is not None
            assert state.gaps is not None
            assert state.storage_corrupt_sessions is not None
            pending: list[ObservationOutboxRow] = []
            moved: list[ObservationOutboxRow] = []
            for row in state.pending_outbox:
                (moved if row.codex_session_id == codex_session_id else pending).append(row)
            if not moved:
                return 0
            state.pending_outbox[:] = pending
            stamp = self._wall_timestamp()
            existing = {
                (
                    entry[0],
                    entry[1].source_identity,
                    canonical_digest(observation_envelope_to_json(entry[1])),
                )
                for entry in state.quarantine
            }
            for row in moved:
                identity = (
                    codex_session_id,
                    row.envelope.source_identity,
                    canonical_digest(observation_envelope_to_json(row.envelope)),
                )
                if identity in existing:
                    continue
                state.quarantine.append((codex_session_id, row.envelope, reason, stamp))
                existing.add(identity)
            while len(state.quarantine) > _MAX_QUARANTINE:
                evicted = state.quarantine.pop(0)
                self._record_quarantine_eviction(state, evicted[0], evicted[1], evicted[2])
            if reason in {gap.value for gap in ObservationGapCode}:
                self._note_gap_state(state, reason)
            if reason == ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value:
                state.storage_corrupt_sessions.add(codex_session_id)
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            self._save(workspace, state)
            return len(moved)

    def quarantined_count(self, workspace: str) -> int:
        with self._lock:
            state = self._load(workspace)
            assert state.quarantine is not None
            return len(state.quarantine)

    def quarantine_facts(self, workspace: str) -> tuple[int, int, int]:
        """Return (quarantine depth, involuntary evictions, operator reclaims).

        The two loss counters are deliberately separate: an eviction is yoetz
        destroying detail on its own (byte cap or age bound), a reclaim is the
        operator deliberately dropping it. Folding them together would let a
        voluntary cleanup read as data loss, or vice versa.
        """

        with self._lock:
            state = self._load(workspace)
            assert state.quarantine is not None
            return (
                len(state.quarantine),
                state.quarantine_evicted_count,
                state.quarantine_reclaimed_count,
            )

    def reclaim_quarantine(self, workspace: str) -> int:
        """Operator-initiated drop of all quarantined observation detail.

        Reclaimed entries extend the same aggregate commitment chain as
        cap/age evictions but are counted separately (operator action, not
        data loss), so a recovered install can shed the per-hook tax without
        the drop becoming silent or reading as destruction.
        Returns how many entries were reclaimed.
        """

        with self._lock:
            state = self._load(workspace)
            assert state.quarantine is not None
            reclaimed = len(state.quarantine)
            if reclaimed == 0:
                return 0
            for entry in state.quarantine:
                self._record_quarantine_eviction(
                    state, entry[0], entry[1], entry[2], reclaimed=True
                )
            state.quarantine.clear()
            self._save(workspace, state)
            return reclaimed

    def list_quarantine(
        self, workspace: str
    ) -> tuple[tuple[str, ObservationEnvelope, str, Timestamp], ...]:
        with self._lock:
            state = self._load(workspace)
            assert state.quarantine is not None
            return tuple(state.quarantine)

    def note_coverage_gap(self, workspace: str, gap_code: str) -> None:
        """Record a safe local coverage gap without retaining payload prose."""

        with self._lock:
            state = self._load(workspace)
            assert state.gaps is not None
            if type(gap_code) is str and gap_code:
                self._note_gap_state(state, gap_code)
            self._save(workspace, state)

    def _note_gap_state(self, state: _WorkspaceState, gap_code: str) -> None:
        assert state.gaps is not None
        observed_at = self._wall_timestamp()
        prior = state.gaps.get(gap_code)
        state.gaps[gap_code] = _GapState(
            observed_at if prior is None else prior.first_seen,
            observed_at,
            True,
        )

    @classmethod
    def _resolve_delivered(cls, state: _WorkspaceState) -> None:
        """Clear the conditions a completed delivery disproves.

        A row that reached the service and was acknowledged is live evidence that the service was
        reachable, the vault was open, and the outbox is no longer over its bound.
        """

        for code in (
            ObservationGapCode.SERVICE_UNAVAILABLE.value,
            ObservationGapCode.VAULT_LOCKED.value,
            _LOCAL_OUTBOX_OVERFLOW_GAP,
        ):
            cls._resolve_gap_state(state, code)

    @staticmethod
    def _resolve_gap_state(state: _WorkspaceState, gap_code: str) -> None:
        assert state.gaps is not None
        prior = state.gaps.get(gap_code)
        if prior is not None:
            state.gaps[gap_code] = _GapState(prior.first_seen, prior.last_seen, False)

    def trust_policy_digest(self, workspace: str, policy_digest: str) -> None:
        """Persist a tamper-evident local activation cache for one exact digest.

        The task-bundle repository remains the authoritative encrypted trust
        record. This cache contains no argv or content and cannot activate a
        different byte digest.
        """

        import hashlib
        import hmac

        if (
            type(policy_digest) is not str
            or not policy_digest.startswith("sha256:")
            or len(policy_digest) != 71
        ):
            raise ProtocolValueError("invalid_approved_check_policy")
        with self._lock:
            state = self._load(workspace)
            state.trusted_policy_digest = policy_digest
            state.trusted_policy_mac = hmac.new(
                self.key_material(),
                b"yoetz/check-policy-trust/v1\0"
                + workspace.encode("ascii")
                + b"\0"
                + policy_digest.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            self._save(workspace, state)

    def policy_digest_is_trusted(self, workspace: str, policy_digest: str) -> bool:
        import hashlib
        import hmac

        with self._lock:
            state = self._load(workspace)
            expected = hmac.new(
                self.key_material(),
                b"yoetz/check-policy-trust/v1\0"
                + workspace.encode("ascii")
                + b"\0"
                + policy_digest.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
            return (
                state.trusted_policy_digest == policy_digest
                and state.trusted_policy_mac is not None
                and hmac.compare_digest(state.trusted_policy_mac, expected)
            )

    def revoke_policy_trust(self, workspace: str) -> None:
        with self._lock:
            state = self._load(workspace)
            state.trusted_policy_digest = None
            state.trusted_policy_mac = None
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
                self._note_gap_state(state, ObservationGapCode.CURSOR_STALE.value)
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
            state.monotonic_epoch = self._boot_epoch()
            if envelope.source is ObservationSource.CODEX_HOOK:
                state.last_hook_receipt_mono_ms = mono_ms
            else:
                state.last_stream_reconcile_mono_ms = mono_ms
            # Accepting an envelope is live proof the cursor advanced past whatever was stale.
            # Content capture is only proven by an envelope that actually carried captured
            # content, so it clears on that narrower evidence and not on ingest alone.
            self._resolve_gap_state(state, ObservationGapCode.CURSOR_STALE.value)
            if envelope.content_object_refs:
                self._resolve_gap_state(state, ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
            assert state.codex_session_bindings is not None
            assert state.storage_corrupt_sessions is not None
            repaired_sessions = {
                codex_session_id
                for codex_session_id, commitment in state.codex_session_bindings.items()
                if commitment == envelope.session_commitment
            }
            state.storage_corrupt_sessions.difference_update(repaired_sessions)
            if not state.storage_corrupt_sessions:
                self._resolve_gap_state(state, ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value)
            for gap in envelope.gap_codes:
                self._note_gap_state(state, gap)
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

    @contextlib.contextmanager
    def batched(self, workspace_commitment: str) -> Generator[None]:
        """Hold one workspace state open across a pass; serialize once at exit.

        Durability trade-off: a SIGKILL inside a batch loses that batch's local
        mutations rather than only the tail. That matches the outbox's design —
        an un-acked row is retried, a lost envelope is re-ingested or recovered
        by stream reconcile — but callers MUST close the batch before any
        service RPC so an outbox acknowledgement can never become durable ahead
        of the ingest it acknowledges, and MUST NOT span a network wait: the
        batch holds the interprocess store lock for its whole duration.
        """

        with self._lock:
            nested = workspace_commitment in self._batch
            if not nested:
                self._batch[workspace_commitment] = self._load(workspace_commitment)
            try:
                yield
            finally:
                if not nested:
                    state = self._batch.pop(workspace_commitment, None)
                    dirty = workspace_commitment in self._batch_dirty
                    self._batch_dirty.discard(workspace_commitment)
                    if state is not None and dirty:
                        self._save(workspace_commitment, state)

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

    def _stat_key(self, path: Path) -> tuple[int, int, int, int] | None:
        try:
            if path.is_symlink():
                return None
            facts = path.stat()
        except OSError:
            return None
        return (facts.st_ino, facts.st_size, facts.st_mtime_ns, facts.st_ctime_ns)

    def _cache_state(
        self,
        workspace_commitment: str,
        key: tuple[int, int, int, int],
        state: _WorkspaceState,
    ) -> None:
        self._state_cache.pop(workspace_commitment, None)
        self._state_cache[workspace_commitment] = (key, _copy_state(state))
        while len(self._state_cache) > _MAX_STATE_CACHE_ENTRIES:
            self._state_cache.pop(next(iter(self._state_cache)))

    def _load(self, workspace_commitment: str) -> _WorkspaceState:
        held = self._batch.get(workspace_commitment)
        if held is not None:
            return held
        path = self._workspace_path(workspace_commitment)
        before = self._stat_key(path)
        cached = self._state_cache.get(workspace_commitment)
        if cached is not None and before is not None and cached[0] == before:
            return _copy_state(cached[1])
        raw = _read_bytes(path, maximum=_MAX_LEGACY_STATE_BYTES)
        if raw is None:
            return _WorkspaceState()
        hydrate_started = self._now_mono()
        try:
            try:
                parsed = strict_json_parse(raw)
            except ProtocolValueError:
                return _WorkspaceState()
            if not isinstance(parsed, Mapping):
                return _WorkspaceState()
            state = self._state_from_json(cast(Mapping[str, JsonValue], parsed))
        finally:
            self.stage_timings_ms["hydrate"] += (self._now_mono() - hydrate_started) * 1000
        # Cache only when the file provably did not change while it was read.
        if before is not None and before == self._stat_key(path):
            self._cache_state(workspace_commitment, before, state)
        return state

    def _prune_expired_quarantine(self, state: _WorkspaceState) -> None:
        assert state.quarantine is not None
        if not state.quarantine:
            return
        # Fence the destructive path on a trusted clock, like every other
        # wall-time consumer in this module: after a reboot, snapshot restore,
        # or clock jump the persisted epoch disagrees and pruning is skipped
        # until fresh progress re-establishes it. Age is measured from the
        # store-authored quarantined_at, never the (possibly far older)
        # envelope receipt time.
        if not self._epoch_matches(state.monotonic_epoch):
            return
        horizon = datetime.fromtimestamp(self._wall_now(), UTC) - timedelta(
            days=_MAX_QUARANTINE_AGE_DAYS
        )
        # RFC3339 wire strings at fixed precision order lexicographically, so
        # this hot-path comparison never reparses timestamps.
        horizon_wire = timestamp_from_datetime(
            horizon.replace(microsecond=(horizon.microsecond // 1000) * 1000)
        ).wire
        kept: list[tuple[str, ObservationEnvelope, str, Timestamp]] = []
        for entry in state.quarantine:
            if entry[3].wire < horizon_wire:
                self._record_quarantine_eviction(state, entry[0], entry[1], entry[2])
            else:
                kept.append(entry)
        if len(kept) != len(state.quarantine):
            state.quarantine[:] = kept

    def _prune_frontier_motion_notices(self, state: _WorkspaceState) -> None:
        """Drop ended-session notices and delivered marks; cap both mappings."""

        ended = state.ended_sessions or set()
        bindings = state.codex_session_bindings or {}

        def _session_ended(session_id: str) -> bool:
            commitment = bindings.get(session_id)
            return commitment is not None and commitment in ended

        notices = state.frontier_motion_notices
        if notices:
            for session_id in tuple(notices):
                if _session_ended(session_id):
                    del notices[session_id]
            while len(notices) > _MAX_FRONTIER_MOTION_NOTICES:
                del notices[next(iter(notices))]
        delivered = state.frontier_motion_delivered
        if delivered:
            for session_id in tuple(delivered):
                if _session_ended(session_id):
                    del delivered[session_id]
            while len(delivered) > _MAX_FRONTIER_MOTION_NOTICES:
                del delivered[next(iter(delivered))]

    def _encode_state(self, workspace_commitment: str, state: _WorkspaceState) -> bytes:
        """Encode one state to its on-disk bytes, attributing the cost (#290)."""

        encode_started = self._now_mono()
        try:
            return canonical_encode(self._state_to_json(workspace_commitment, state)) + b"\n"
        finally:
            self.stage_timings_ms["encode"] += (self._now_mono() - encode_started) * 1000

    def _drop_oversized_stream_partials(self, state: _WorkspaceState) -> bool:
        """Enforce the per-entry partial bound on states persisted before #289."""

        partials = state.stream_partials
        dropped_sessions = state.stream_partial_dropped_sessions
        assert partials is not None
        assert dropped_sessions is not None
        oversized = [
            key for key, value in partials.items() if len(value) > _MAX_STREAM_PARTIAL_BYTES
        ]
        for key in oversized:
            del partials[key]
            dropped_sessions.add(key)
            self._note_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
        return bool(oversized)

    def _save(
        self,
        workspace_commitment: str,
        state: _WorkspaceState,
        *,
        projected: bytes | None = None,
    ) -> None:
        """Serialize one workspace state, trimming to the safe local bound.

        ``projected`` reuses bytes a caller already encoded for a size check;
        they are discarded when pruning mutated the state after that encode.
        """

        if self._batch.get(workspace_commitment) is state:
            self._batch_dirty.add(workspace_commitment)
            return
        directory = self._root / "workspaces"
        _ensure_dir(directory)
        path = self._workspace_path(workspace_commitment)
        quarantined_before = len(state.quarantine or ())
        notices_before = len(state.frontier_motion_notices or ())
        delivered_before = len(state.frontier_motion_delivered or ())
        self._prune_expired_quarantine(state)
        self._prune_frontier_motion_notices(state)
        partials_dropped = self._drop_oversized_stream_partials(state)
        if (
            projected is not None
            and not partials_dropped
            and len(state.quarantine or ()) == quarantined_before
            and len(state.frontier_motion_notices or ()) == notices_before
            and len(state.frontier_motion_delivered or ()) == delivered_before
        ):
            payload = projected
        else:
            payload = self._encode_state(workspace_commitment, state)
        partials = state.stream_partials
        dropped_sessions = state.stream_partial_dropped_sessions
        assert partials is not None
        assert dropped_sessions is not None
        while partials and len(payload) > _MAX_STATE_BYTES:
            # Shed the read-cache before any durable row: a dropped partial
            # is reread from the committed cursor on the next reconcile,
            # while an evicted envelope is a lost observation (#289).
            largest = max(partials, key=lambda key: (len(partials[key]), key.encode()))
            del partials[largest]
            dropped_sessions.add(largest)
            self._note_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
            payload = self._encode_state(workspace_commitment, state)
        truncated = False
        if len(payload) > _MAX_STATE_BYTES:
            # Retain authority state and make every observation-detail loss explicit.
            assert state.envelopes is not None
            while state.envelopes and len(payload) > _MAX_STATE_BYTES:
                del state.envelopes[0]
                assert state.gaps is not None
                self._note_gap_state(state, ObservationGapCode.TRUNCATED_PAYLOAD.value)
                truncated = True
                payload = self._encode_state(workspace_commitment, state)
        assert state.pending_outbox is not None
        assert state.quarantine is not None
        assert state.gaps is not None
        while state.pending_outbox and len(payload) > _MAX_STATE_BYTES:
            row = state.pending_outbox.pop(0)
            already = any(
                entry[0] == row.codex_session_id
                and observation_envelope_to_json(entry[1])
                == observation_envelope_to_json(row.envelope)
                for entry in state.quarantine
            )
            if not already:
                state.quarantine.append(
                    (
                        row.codex_session_id,
                        row.envelope,
                        ObservationGapCode.OUTBOX_OVERFLOW.value,
                        self._wall_timestamp(),
                    )
                )
            self._note_gap_state(state, _LOCAL_OUTBOX_OVERFLOW_GAP)
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            payload = self._encode_state(workspace_commitment, state)
        while state.quarantine and len(payload) > _MAX_STATE_BYTES:
            evicted = state.quarantine.pop(0)
            self._record_quarantine_eviction(state, evicted[0], evicted[1], evicted[2])
            payload = self._encode_state(workspace_commitment, state)
        if len(payload) > _MAX_STATE_BYTES:
            raise _error(
                PublicErrorCode.STORAGE_UNSAFE,
                "Observation state exceeds its safe local bound.",
                retryable=False,
            )
        truncation = state.gaps.get(ObservationGapCode.TRUNCATED_PAYLOAD.value)
        if (
            not truncated
            and truncation is not None
            and truncation.active
            and len(payload) + _MAX_STATE_BYTES // _STATE_HEADROOM_DIVISOR <= _MAX_STATE_BYTES
        ):
            # Landing with a full headroom margin, having shed nothing, is live
            # proof the store is no longer losing observations to the bound.
            # Landing merely *under* the bound proves nothing — that is the
            # state an eviction itself leaves behind, so clearing there would
            # retire the gap in the same pass that opened it. History stays in
            # gap_history; only the active flag, which reports live
            # degradation, is cleared (#310).
            self._resolve_gap_state(state, ObservationGapCode.TRUNCATED_PAYLOAD.value)
            payload = self._encode_state(workspace_commitment, state)
        write_started = self._now_mono()
        _atomic_write(path, payload)
        self.stage_timings_ms["write"] += (self._now_mono() - write_started) * 1000
        key = self._stat_key(path)
        if key is None:
            self._state_cache.pop(workspace_commitment, None)
        else:
            self._cache_state(workspace_commitment, key, state)

    def _record_quarantine_eviction(
        self,
        state: _WorkspaceState,
        codex_session_id: str,
        envelope: ObservationEnvelope,
        reason: str,
        *,
        reclaimed: bool = False,
    ) -> None:
        assert state.gaps is not None
        material = JsonObject(
            {
                "prior": state.quarantine_evicted_commitment,
                "session_commitment": envelope.session_commitment,
                "source_identity": envelope.source_identity,
                "source_commitment": envelope.cursor.last_source_commitment,
                "reason": reason,
                "reclaimed": reclaimed,
                "codex_session_commitment": session_commitment_from_codex_id(
                    self._cached_key_material(), codex_session_id
                ),
            }
        )
        state.quarantine_evicted_commitment = canonical_digest(material)
        if reclaimed:
            state.quarantine_reclaimed_count += 1
        else:
            state.quarantine_evicted_count += 1
        receipt = envelope.receipt_time
        if state.quarantine_evicted_first is None or receipt < state.quarantine_evicted_first:
            state.quarantine_evicted_first = receipt
        if state.quarantine_evicted_last is None or state.quarantine_evicted_last < receipt:
            state.quarantine_evicted_last = receipt
        self._note_gap_state(state, ObservationGapCode.QUARANTINE_DETAIL_EVICTED.value)

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
        # Fence persisted monotonic samples to their boot epoch. After a restart
        # or reboot the monotonic clock is incomparable, so drop the stale
        # samples; lifecycle then reports DEGRADED until fresh qualifying
        # progress arrives in the current epoch instead of trusting bad ages.
        if not self._epoch_matches(state.monotonic_epoch):
            last_hook = None
            last_stream = None
            last_drain = None
        consent_active = consent is not None and consent.revoked_at is None and not consent.paused
        mapping_available = bool(state.session_workspaces) or bool(state.codex_session_bindings)
        current_gaps = self._current_gaps(state, mapping_available=mapping_available)
        bound_sessions = set(state.session_workspaces)
        ended_sessions = state.ended_sessions or set()
        # STOPPED once every bound session has ended (consent-stop is handled in
        # compute_observation_lifecycle via consent_active).
        session_ended = bool(bound_sessions) and bound_sessions <= ended_sessions
        signals = ObservationHealthSignals(
            consent_active=consent_active,
            mapping_available=mapping_available,
            source_coverage=coverage,
            pending_outbox_count=pending,
            # Delivery backlog is reported independently as pending_outbox_count.
            # No source-frontier lag estimator is available at this local seam,
            # so never relabel undelivered rows as observed event lag.
            lag_events=0,
            gaps=current_gaps,
            unsupported_events=tuple(sorted(state.unsupported_events, key=str.encode)),
            advice_frontier=state.advice_frontier,
            last_hook_receipt_monotonic=last_hook,
            last_stream_advancement_monotonic=last_stream,
            last_successful_drain_monotonic=last_drain if pending == 0 else last_drain,
            session_ended=session_ended,
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
            lag_events=0,
            gaps=current_gaps,
            unsupported_events=tuple(sorted(state.unsupported_events, key=str.encode)),
            advice_frontier=state.advice_frontier,
        )

    def _current_gaps(self, state: _WorkspaceState, *, mapping_available: bool) -> tuple[str, ...]:
        """Project current observable gaps while retaining full history separately."""

        assert state.gaps is not None
        assert state.pending_outbox is not None
        assert state.quarantine is not None
        # Codes re-derived below from live state. Everything else reports its recorded active
        # flag, which ``_resolve_gap_state`` clears when a condition is observed to have healed.
        # Note that gap sightings are stamped with the local wall clock while ``last_receipt`` is
        # a caller-asserted envelope time, so the two are never compared: resolution is driven by
        # observed signals, never by ordering one clock against the other.
        transient = {
            ObservationGapCode.MAPPING_MISSING.value,
            ObservationGapCode.OUTBOX_OVERFLOW.value,
            ObservationGapCode.OUTBOX_QUARANTINED.value,
            _LOCAL_OUTBOX_OVERFLOW_GAP,
            _LOCAL_STREAM_PARTIAL_DROPPED_GAP,
        }
        current = {code for code, seen in state.gaps.items() if seen.active} - transient
        # A dropped stream partial means the source tail is pending a reread:
        # the stream is observably behind its source until reconcile catches
        # up, which is exactly SOURCE_LAG on the wire vocabulary (#289).
        partial_gap = state.gaps.get(_LOCAL_STREAM_PARTIAL_DROPPED_GAP)
        if partial_gap is not None and partial_gap.active:
            current.add(ObservationGapCode.SOURCE_LAG.value)
        if state.quarantine:
            current.add(ObservationGapCode.OUTBOX_QUARANTINED.value)
        overflow_gap = state.gaps.get(_LOCAL_OUTBOX_OVERFLOW_GAP)
        if len(state.pending_outbox) >= _MAX_OUTBOX or (
            overflow_gap is not None and overflow_gap.active
        ):
            current.add(ObservationGapCode.OUTBOX_OVERFLOW.value)
        source_overflow = state.gaps.get(ObservationGapCode.OUTBOX_OVERFLOW.value)
        if source_overflow is not None and source_overflow.active:
            current.add(ObservationGapCode.OUTBOX_OVERFLOW.value)
        for row in state.pending_outbox:
            # A row deferred behind an ADR-022 check barrier is designed
            # back-pressure awaiting retry, never a current coverage gap (#351);
            # the row keeps its honest last_reason annotation without it.
            if row.last_reason is not None and row.last_reason != OBSERVATION_BACKPRESSURE_REASON:
                current.add(row.last_reason)
        # Live mapping presence outranks both the latched code and any stale row reason: a row
        # rejected for a missing mapping keeps that reason after the mapping is restored, and
        # reporting it again is the exact defect #219 filed.
        if mapping_available:
            current.discard(ObservationGapCode.MAPPING_MISSING.value)
        elif state.gaps.get(ObservationGapCode.MAPPING_MISSING.value) is not None:
            current.add(ObservationGapCode.MAPPING_MISSING.value)
        return tuple(sorted(current, key=str.encode))

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
        payload: dict[str, JsonValue] = {
            # /7 attributes dropped stream-partial gaps per session. /6 adds one-shot
            # task-frontier motion notices. /5 adds terminal
            # corruption-session tracking. /3 added quarantined_at per
            # quarantine entry and the reclaimed counter. Readers tolerate both directions:
            # unknown keys are ignored and missing keys default safely.
            "schema": "yoetz.observation-local/7",
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
            "ended_sessions": tuple(sorted(state.ended_sessions or set(), key=str.encode)),
            "session_generations": JsonObject(
                {
                    key: value
                    for key, value in sorted(
                        (state.session_generations or {}).items(),
                        key=lambda item: item[0].encode(),
                    )
                }
            ),
            "ended_session_generations": JsonObject(
                {
                    key: value
                    for key, value in sorted(
                        (state.ended_session_generations or {}).items(),
                        key=lambda item: item[0].encode(),
                    )
                }
            ),
            "envelopes": tuple(observation_envelope_to_json(item) for item in state.envelopes),
            "gaps": tuple(sorted(state.gaps, key=str.encode)),
            "gap_history": JsonObject(
                {
                    code: JsonObject(
                        {
                            "first_seen": seen.first_seen.wire,
                            "last_seen": seen.last_seen.wire,
                            "active": seen.active,
                        }
                    )
                    for code, seen in sorted(state.gaps.items(), key=lambda item: item[0].encode())
                }
            ),
            "unsupported_events": tuple(sorted(state.unsupported_events, key=str.encode)),
            "last_receipt": None if state.last_receipt is None else state.last_receipt.wire,
            "advice_frontier": state.advice_frontier,
            "advice_snapshot": (
                None
                if state.advice_snapshot is None
                else advice_snapshot_to_json(state.advice_snapshot)
            ),
            "last_advice_suppression": state.last_advice_suppression,
            "session_advice": JsonObject(
                {
                    key: advice_snapshot_to_json(snapshot)
                    for key, snapshot in sorted(
                        (state.session_advice or {}).items(), key=lambda item: item[0].encode()
                    )
                }
            ),
            "session_advice_suppression": JsonObject(
                {
                    key: value
                    for key, value in sorted(
                        (state.session_advice_suppression or {}).items(),
                        key=lambda item: item[0].encode(),
                    )
                }
            ),
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
            # Canonical JSON forbids floats; persist the epoch as integer millis.
            "monotonic_epoch_ms": (
                None if state.monotonic_epoch is None else round(state.monotonic_epoch * 1000)
            ),
            "pending_outbox": tuple(
                JsonObject(
                    {
                        "codex_session_id": row.codex_session_id,
                        "envelope": observation_envelope_to_json(row.envelope),
                        "attempts": row.attempts,
                        "last_reason": row.last_reason,
                        "last_attempt_at": (
                            None if row.last_attempt_at is None else row.last_attempt_at.wire
                        ),
                    }
                )
                for row in (state.pending_outbox or ())
            ),
            "quarantine": tuple(
                JsonObject(
                    {
                        "codex_session_id": entry[0],
                        "envelope": observation_envelope_to_json(entry[1]),
                        "reason": entry[2],
                        "quarantined_at": entry[3].wire,
                    }
                )
                for entry in (state.quarantine or ())
            ),
            "quarantine_evicted_count": state.quarantine_evicted_count,
            "quarantine_reclaimed_count": state.quarantine_reclaimed_count,
            "quarantine_evicted_commitment": state.quarantine_evicted_commitment,
            "quarantine_evicted_first": (
                None
                if state.quarantine_evicted_first is None
                else state.quarantine_evicted_first.wire
            ),
            "quarantine_evicted_last": (
                None
                if state.quarantine_evicted_last is None
                else state.quarantine_evicted_last.wire
            ),
            "trusted_policy_digest": state.trusted_policy_digest,
            "trusted_policy_mac": state.trusted_policy_mac,
            "codex_session_bindings": JsonObject(
                {key: value for key, value in sorted(state.codex_session_bindings.items())}
            ),
        }
        if state.storage_corrupt_sessions:
            payload["storage_corrupt_sessions"] = tuple(
                sorted(state.storage_corrupt_sessions, key=str.encode)
            )
        if state.frontier_motion_notices:
            payload["frontier_motion_notices"] = JsonObject(
                {
                    key: JsonObject(
                        {
                            "from_sequence": notice.from_sequence,
                            "head_digest": notice.head_digest,
                            "observation_record_count": notice.observation_record_count,
                            "task_id": notice.task_id,
                            "to_sequence": notice.to_sequence,
                        }
                    )
                    for key, notice in sorted(
                        state.frontier_motion_notices.items(),
                        key=lambda item: item[0].encode(),
                    )
                }
            )
        if state.frontier_motion_delivered:
            payload["frontier_motion_delivered"] = JsonObject(
                {
                    key: JsonObject({"task_id": value[0], "to_sequence": value[1]})
                    for key, value in sorted(
                        state.frontier_motion_delivered.items(),
                        key=lambda item: item[0].encode(),
                    )
                }
            )
        if state.stream_partial_dropped_sessions:
            payload["stream_partial_dropped_sessions"] = tuple(
                sorted(state.stream_partial_dropped_sessions, key=str.encode)
            )
        return payload

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
        ended_sessions_raw = raw.get("ended_sessions") or ()
        session_generations = {
            str(key): int(value)
            for key, value in cast(
                Mapping[str, JsonValue], raw.get("session_generations") or {}
            ).items()
            if type(value) is int and not isinstance(value, bool) and value >= 1
        }
        ended_session_generations = {
            str(key): int(value)
            for key, value in cast(
                Mapping[str, JsonValue], raw.get("ended_session_generations") or {}
            ).items()
            if type(value) is int and not isinstance(value, bool) and value >= 1
        }
        envelopes_raw = raw.get("envelopes") or ()
        gaps_raw = raw.get("gaps") or ()
        gap_history: dict[str, _GapState] = {}
        raw_gap_history = raw.get("gap_history") or {}
        if isinstance(raw_gap_history, Mapping):
            for code, value in cast(Mapping[str, JsonValue], raw_gap_history).items():
                if type(code) is not str or not isinstance(value, Mapping):
                    continue
                seen = cast(Mapping[str, JsonValue], value)
                first_seen = seen.get("first_seen")
                last_seen = seen.get("last_seen")
                if type(first_seen) is not str or type(last_seen) is not str:
                    continue
                try:
                    gap_history[code] = _GapState(
                        Timestamp(first_seen),
                        Timestamp(last_seen),
                        seen.get("active", True) is True,
                    )
                except ProtocolValueError, TypeError, ValueError:
                    continue
        legacy_seen = self._wall_timestamp()
        for code in cast(tuple[str, ...], gaps_raw):
            if type(code) is str and code not in gap_history:
                gap_history[code] = _GapState(legacy_seen, legacy_seen)
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
            except ValueError, OSError:
                continue
        dropped_sessions_raw = raw.get("stream_partial_dropped_sessions")
        stream_partial_dropped_sessions: set[str]
        if isinstance(dropped_sessions_raw, (list, tuple)):
            stream_partial_dropped_sessions = {
                value
                for value in cast(tuple[JsonValue, ...] | list[JsonValue], dropped_sessions_raw)
                if type(value) is str and value
            }
        else:
            legacy_gap = gap_history.get(_LOCAL_STREAM_PARTIAL_DROPPED_GAP)
            stream_partial_dropped_sessions = (
                set(stream_cursors) | set(session_workspaces)
                if legacy_gap is not None and legacy_gap.active
                else set[str]()
            )
            if legacy_gap is not None and legacy_gap.active and not stream_partial_dropped_sessions:
                stream_partial_dropped_sessions.add(_LEGACY_STREAM_PARTIAL_DROPPED_SESSION)
        hook_sequences: dict[str, int] = {}
        for key, value in cast(Mapping[str, JsonValue], raw.get("hook_sequences") or {}).items():
            if type(value) is int and not isinstance(value, bool) and value >= 0:
                hook_sequences[str(key)] = value
        reconcile_mono = raw.get("last_stream_reconcile_mono_ms")
        last_reconcile = (
            int(reconcile_mono)
            if type(reconcile_mono) is int
            and not isinstance(reconcile_mono, bool)
            and reconcile_mono >= 0
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
        epoch_raw = raw.get("monotonic_epoch_ms")
        monotonic_epoch = (
            float(epoch_raw) / 1000.0
            if type(epoch_raw) is int and not isinstance(epoch_raw, bool)
            else None
        )
        bindings = {
            str(key): str(value)
            for key, value in cast(
                Mapping[str, JsonValue], raw.get("codex_session_bindings") or {}
            ).items()
        }
        storage_corrupt_sessions = {
            str(value)
            for value in cast(
                tuple[JsonValue, ...] | list[JsonValue],
                raw.get("storage_corrupt_sessions") or (),
            )
            if type(value) is str and value
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
        pending_outbox: list[ObservationOutboxRow] = []
        for item in cast(tuple[JsonValue, ...] | list[JsonValue], raw.get("pending_outbox") or ()):
            if not isinstance(item, Mapping):
                continue
            row = cast(Mapping[str, JsonValue], item)
            session = row.get("codex_session_id")
            envelope_raw = row.get("envelope")
            if type(session) is not str or not isinstance(envelope_raw, Mapping):
                continue
            attempts_raw = row.get("attempts", 0)
            attempts = (
                attempts_raw
                if type(attempts_raw) is int
                and not isinstance(attempts_raw, bool)
                and 0 <= attempts_raw <= _MAX_SAFE_INTEGER
                else 0
            )
            last_reason_raw = row.get("last_reason")
            last_reason = (
                last_reason_raw
                if type(last_reason_raw) is str
                and _OUTBOX_REASON_RE.fullmatch(last_reason_raw) is not None
                else None
            )
            last_attempt_raw = row.get("last_attempt_at")
            try:
                last_attempt_at = (
                    None if last_attempt_raw is None else Timestamp(str(last_attempt_raw))
                )
            except ProtocolValueError, TypeError, ValueError:
                last_attempt_at = None
            try:
                pending_outbox.append(
                    ObservationOutboxRow(
                        codex_session_id=session,
                        envelope=observation_envelope_from_json(
                            JsonObject(cast(Mapping[str, JsonValue], envelope_raw))
                        ),
                        attempts=attempts,
                        last_reason=last_reason,
                        last_attempt_at=last_attempt_at,
                    )
                )
            except ProtocolValueError, TypeError, ValueError:
                continue
        quarantine: list[tuple[str, ObservationEnvelope, str, Timestamp]] = []
        # Entries written before quarantined_at existed default to load time:
        # their true quarantine age is unknown, so the age bound restarts
        # rather than destroying them retroactively.
        quarantined_at_default = self._wall_timestamp()
        for item in cast(tuple[JsonValue, ...] | list[JsonValue], raw.get("quarantine") or ()):
            if not isinstance(item, Mapping):
                continue
            row = cast(Mapping[str, JsonValue], item)
            session = row.get("codex_session_id")
            envelope_raw = row.get("envelope")
            reason = row.get("reason")
            if (
                type(session) is not str
                or type(reason) is not str
                or not isinstance(envelope_raw, Mapping)
            ):
                continue
            raw_quarantined_at = row.get("quarantined_at")
            try:
                quarantined_at = (
                    Timestamp(raw_quarantined_at)
                    if type(raw_quarantined_at) is str
                    else quarantined_at_default
                )
                quarantine.append(
                    (
                        session,
                        observation_envelope_from_json(
                            JsonObject(cast(Mapping[str, JsonValue], envelope_raw))
                        ),
                        reason,
                        quarantined_at,
                    )
                )
            except ProtocolValueError, TypeError, ValueError:
                continue
        raw_quarantine_evicted_count = raw.get("quarantine_evicted_count", 0)
        quarantine_evicted_count = (
            raw_quarantine_evicted_count if type(raw_quarantine_evicted_count) is int else 0
        )
        raw_quarantine_reclaimed_count = raw.get("quarantine_reclaimed_count", 0)
        quarantine_reclaimed_count = (
            raw_quarantine_reclaimed_count if type(raw_quarantine_reclaimed_count) is int else 0
        )
        frontier_motion_notices = _load_frontier_motion_notices(raw.get("frontier_motion_notices"))
        frontier_motion_delivered = _load_frontier_motion_delivered(
            raw.get("frontier_motion_delivered")
        )
        return _WorkspaceState(
            consent=consent,
            session_workspaces=session_workspaces,
            cursors=cursors,
            dedup=set(cast(tuple[str, ...], dedup_raw)),
            ended_sessions=set(cast(tuple[str, ...], ended_sessions_raw)),
            session_generations=session_generations,
            ended_session_generations=ended_session_generations,
            envelopes=envelopes,
            gaps=gap_history,
            unsupported_events=set(cast(tuple[str, ...], unsupported_raw)),
            last_receipt=None if last_receipt is None else Timestamp(str(last_receipt)),
            advice_frontier=cast(str | None, raw.get("advice_frontier")),
            advice_snapshot=advice_snapshot,
            last_advice_suppression=cast(str | None, raw.get("last_advice_suppression")),
            session_advice=_load_session_advice(raw.get("session_advice")),
            session_advice_suppression={
                key: value
                for key, value in cast(
                    Mapping[str, JsonValue], raw.get("session_advice_suppression") or {}
                ).items()
                if type(key) is str and type(value) is str
            },
            frontier_motion_notices=frontier_motion_notices,
            frontier_motion_delivered=frontier_motion_delivered,
            open_pre=open_pre,
            stream_cursors=stream_cursors,
            stream_partials=stream_partials,
            stream_partial_dropped_sessions=stream_partial_dropped_sessions,
            hook_sequences=hook_sequences,
            last_stream_reconcile_mono_ms=last_reconcile,
            last_hook_receipt_mono_ms=last_hook_mono,
            last_successful_drain_mono_ms=last_drain_mono,
            monotonic_epoch=monotonic_epoch,
            pending_outbox=pending_outbox,
            quarantine=quarantine,
            quarantine_evicted_count=quarantine_evicted_count,
            quarantine_reclaimed_count=quarantine_reclaimed_count,
            quarantine_evicted_commitment=cast(
                str | None, raw.get("quarantine_evicted_commitment")
            ),
            quarantine_evicted_first=(
                None
                if raw.get("quarantine_evicted_first") is None
                else Timestamp(str(raw.get("quarantine_evicted_first")))
            ),
            quarantine_evicted_last=(
                None
                if raw.get("quarantine_evicted_last") is None
                else Timestamp(str(raw.get("quarantine_evicted_last")))
            ),
            trusted_policy_digest=cast(str | None, raw.get("trusted_policy_digest")),
            trusted_policy_mac=cast(str | None, raw.get("trusted_policy_mac")),
            codex_session_bindings=bindings,
            storage_corrupt_sessions=storage_corrupt_sessions,
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
