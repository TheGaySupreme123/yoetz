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
from collections.abc import Callable, Generator, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, TypeVar, cast

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
    Frontier,
    JsonObject,
    JsonValue,
    Timestamp,
    timestamp_from_datetime,
    validate_commitment,
    validate_sha256_digest,
)
from yoetz.ports.integrations import YOETZ_WORKFLOW_TOOL_NAMES
from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, validate_id

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
    "PendingSessionLifecycle",
    "STREAM_MAPPING_VERSION",
    "YOETZ_OWNED_TOOL_NAMES",
    "YOETZ_READ_TOOL_NAMES",
    "YOETZ_TOOL_NAMES",
    "observation_dir",
    "self_observation_deliverable",
    "session_commitment_from_codex_id",
    "workspace_commitment_for_path",
]

HOOK_MAPPING_VERSION: Final = "codex-obs-hook/1.0.0"
# 1.3.0: a stream cursor is paired with the exact rollout profile its generation's header
# admitted; cursors persisted under 1.2.0 carry no profile and replay from the header (#568).
STREAM_MAPPING_VERSION: Final = "codex-obs-stream/1.3.0"
_KEY_BYTES: Final = 32
_MAX_STATE_BYTES: Final = 1_048_576
_MAX_LEGACY_STATE_BYTES: Final = 36 * 1_048_576
_MAX_ENVELOPES: Final = 256
_MAX_DEDUP: Final = 4_096
_MAX_OPEN_PRE: Final = 256
_MAX_OUTBOX: Final = 512
_MAX_PENDING_LIFECYCLES: Final = 256
_MAX_PENDING_CONSENT_PROJECTS: Final = 256
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
_MAX_STREAM_CALL_TOOLS: Final = 256
# Parse-cache entry bound: hooks touch one workspace, the daemon's sweep loop
# touches all of them — without a cap the daemon would retain a parsed object
# graph per workspace forever.
_MAX_STATE_CACHE_ENTRIES: Final = 8
_MAX_HOOK_SEQUENCES: Final = 256
_MAX_FRONTIER_MOTION_NOTICES: Final = 256
# Session keyed replay/tombstone indexes have to be bounded independently of
# the envelope and outbox ceilings.  A host can create ended sessions without
# retaining any envelopes, so the byte cap alone does not bound these maps.
_MAX_SESSION_REPLAY_KEYS: Final = 256
_MAX_SESSION_GAP_CODES: Final = 8
_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991

_SessionMapValue = TypeVar("_SessionMapValue")
# Wall/monotonic drift tolerated before persisted monotonic samples are treated
# as belonging to a different boot epoch (and therefore fenced off).
_EPOCH_TOLERANCE_SECONDS: Final = 2.0
_OUTBOX_REASON_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_OBSERVATION_GAP_CODES: Final = frozenset(item.value for item in ObservationGapCode)
_RUNTIME_GATE_SCHEMA: Final = "yoetz.observation-runtime-gate/1"
_RUNTIME_GATE_NAME: Final = "runtime-gate.json"
_MAX_RUNTIME_GATE_BYTES: Final = 256
# Never a legal character in an event-kind token. An interim build stamped
# hook timing after the kind as ``<kind>|<...>``; the reader below still trims
# it so such a value can never be mistaken for an event kind.
_OPEN_PRE_SEPARATOR: Final = "|"
_LOCAL_OUTBOX_OVERFLOW_GAP: Final = "_local_outbox_overflow"
_LOCAL_STREAM_PARTIAL_DROPPED_GAP: Final = "_local_stream_partial_dropped"
_LOCAL_DEDUP_EVICTED_GAP: Final = "_local_dedup_evicted"
_LOCAL_ENVELOPE_RETENTION_GAP: Final = "_local_envelope_retention"
_LEGACY_STREAM_PARTIAL_DROPPED_SESSION: Final = "_legacy_unknown"
_STORE_LOCK_TIMEOUT_SECONDS: Final = 2.0
_STORE_LOCK_POLL_SECONDS: Final = 0.01
# Fraction of the state bound a save must leave free before an eviction gap is
# treated as healed (#310).
_STATE_HEADROOM_DIVISOR: Final = 8

# Yoetz's own MCP tools as Codex spells them (bare registry name or the
# ``mcp__yoetz__`` server prefix). Derived from the one registry tuple so this
# set cannot drift from the tools the bridge actually serves (#564 found the
# hand-written predecessor missing ``read_guidance``).
YOETZ_TOOL_NAMES: Final = frozenset(
    f"{prefix}{name}" for prefix in ("", "mcp__yoetz__") for name in YOETZ_WORKFLOW_TOOL_NAMES
)
# Every host spelling of a Yoetz-owned tool: Codex ``mcp__yoetz__``, Claude
# Code's plugin scope ``mcp__plugin_yoetz_yoetz__`` (host_admission), and the
# Cursor ``server:tool`` forms for the external and plugin-bundled server names.
_YOETZ_TOOL_PREFIXES: Final = (
    "",
    "mcp__yoetz__",
    "mcp__plugin_yoetz_yoetz__",
    "yoetz:",
    "plugin-yoetz-yoetz:",
)
YOETZ_OWNED_TOOL_NAMES: Final = frozenset(
    f"{prefix}{name}" for prefix in _YOETZ_TOOL_PREFIXES for name in YOETZ_WORKFLOW_TOOL_NAMES
)
# Yoetz tools that only read Yoetz's own state. Their result is a projection the
# service already holds, so a successful call is not distinct evidence.
_YOETZ_READ_TOOL_BASENAMES: Final = ("status", "receipt", "read_guidance")
YOETZ_READ_TOOL_NAMES: Final = frozenset(
    f"{prefix}{name}" for prefix in _YOETZ_TOOL_PREFIXES for name in _YOETZ_READ_TOOL_BASENAMES
)
_SELF_OBSERVATION_PHASES: Final = frozenset({"PreToolUse", "PostToolUse"})
_SUCCESS_RESULT_STATUS: Final = frozenset({"complete", "completed", "ok", "success", "succeeded"})


def _explicit_host_failure(structural: Mapping[str, JsonValue]) -> bool:
    """True when the host stated any failure or denial fact for the call.

    Mirrors the materializer's rule that an explicit failure signal wins over
    any success signal: ``success=false``, ``denied=true``, a non-zero
    ``exit_status``, a ``result_status`` outside the closed success vocabulary,
    or Claude Code's ``PostToolUseFailure`` label. A payload with no outcome
    fact is not a failure.
    """

    if structural.get("success") is False or structural.get("denied") is True:
        return True
    exit_status = structural.get("exit_status")
    if type(exit_status) is int and not isinstance(exit_status, bool) and exit_status != 0:
        return True
    result_status = structural.get("result_status")
    if type(result_status) is str and result_status.lower() not in _SUCCESS_RESULT_STATUS:
        return True
    return structural.get("action") == "claude_mcp_failure"


def self_observation_deliverable(phase: str, structural: Mapping[str, JsonValue]) -> bool:
    """Decide whether a hook or stream tool observation carries distinct evidence (#564).

    Every Yoetz tool call the agent makes fires host hooks (and lands in the
    Codex session stream) exactly like any other tool, so Yoetz observing its
    own ``status``/``respond``/``check`` traffic produced two outbox rows plus a
    content capture per call, while the same hook process was trying to drain
    that outbox: the workflow starved its own delivery. The service already
    holds the authoritative record of every Yoetz-owned call it served, so an
    observation of one is distinct evidence only when it says something the
    service cannot know from serving the call:

    - any tool that is not Yoetz-owned: always deliverable (unchanged);
    - an explicit host failure or denial of a Yoetz-owned call: deliverable,
      whatever the tool or phase;
    - the pre-event of a Yoetz-owned call: retained in the local observation
      store only (pairing bookkeeping is unaffected; a delivered post-event
      still materializes the action);
    - the post-event of a Yoetz-owned *read* (``status``, ``receipt``,
      ``read_guidance``) without a stated failure: retained locally only, the
      result is a projection of Yoetz's own state;
    - the post-event of a Yoetz-owned *mutation* (``start``, ``publish_work``,
      ``check``, ``respond``): deliverable, one row per call.

    The envelope is always ingested into the bounded local store first; this
    governs only outbox delivery, so nothing is dropped from local evidence.
    """

    tool = structural.get("tool_name")
    if type(tool) is not str or tool not in YOETZ_OWNED_TOOL_NAMES:
        return True
    if phase not in _SELF_OBSERVATION_PHASES:
        return True
    if _explicit_host_failure(structural):
        return True
    if phase == "PreToolUse":
        return False
    return tool not in YOETZ_READ_TOOL_NAMES


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
    # Persistence-only LRU clock. It is deliberately excluded from notice
    # equality and delivery identity: touching an entry must not change the
    # bytes the hook already peeked.
    recency_ordinal: int = dataclasses.field(default=0, compare=False, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.from_sequence) is not int
            or type(self.to_sequence) is not int
            or type(self.observation_record_count) is not int
            or type(self.task_id) is not str
            or type(self.recency_ordinal) is not int
            or not self.task_id
            or not 0 <= self.from_sequence < self.to_sequence <= _MAX_SAFE_INTEGER
            or not 1 <= self.observation_record_count <= self.to_sequence - self.from_sequence
            or not 0 <= self.recency_ordinal <= _MAX_SAFE_INTEGER
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
class _FrontierMotionDelivered:
    """One lineage-bound delivered mark with a persistence-stable LRU clock."""

    task_id: str
    to_sequence: int
    head_digest: str
    recency_ordinal: int

    def __post_init__(self) -> None:
        if (
            type(self.task_id) is not str
            or not self.task_id
            or type(self.to_sequence) is not int
            or not 1 <= self.to_sequence <= _MAX_SAFE_INTEGER
            or type(self.recency_ordinal) is not int
            or not 0 <= self.recency_ordinal <= _MAX_SAFE_INTEGER
        ):
            raise ProtocolValueError("invalid_event_value_type")
        validate_sha256_digest(self.head_digest)


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
    consecutive_reason_attempts: int = 0

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
        if (
            type(self.consecutive_reason_attempts) is not int
            or isinstance(self.consecutive_reason_attempts, bool)
            or not 0 <= self.consecutive_reason_attempts <= self.attempts
        ):
            raise ProtocolValueError("invalid_event_value_type")


@dataclass(frozen=True, slots=True)
class PendingSessionLifecycle:
    """One durable host-session lifecycle operation waiting for its lock.

    The target generation is frozen when the hook captures the event.  That
    makes retries idempotent: a later worker can distinguish an already
    applied operation from a still-pending one without inferring intent from
    the current ended flag alone.
    """

    codex_session_id: str
    session_commitment: str
    event_kind: str
    target_generation: int
    clear_mapping: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.codex_session_id) is not str
            or not 1 <= len(self.codex_session_id) <= 128
            or "/" in self.codex_session_id
            or "\\" in self.codex_session_id
            or "\0" in self.codex_session_id
            or not self.codex_session_id.isascii()
            or not all(0x21 <= ord(char) <= 0x7E for char in self.codex_session_id)
        ):
            raise ProtocolValueError("invalid_event_value_type")
        if type(self.session_commitment) is not str or not re.fullmatch(
            r"hmac-sha256:[0-9a-f]{64}", self.session_commitment
        ):
            raise ProtocolValueError("invalid_commitment")
        if self.event_kind not in {"SessionStart", "SessionEnd"}:
            raise ProtocolValueError("invalid_event_value_type")
        if (
            type(self.target_generation) is not int
            or isinstance(self.target_generation, bool)
            or not 1 <= self.target_generation <= _MAX_SAFE_INTEGER
        ):
            raise ProtocolValueError("invalid_event_value_type")
        if type(self.clear_mapping) is not bool:
            raise ProtocolValueError("invalid_event_value_type")
        if self.clear_mapping and self.event_kind != "SessionStart":
            raise ProtocolValueError("invalid_event_value_type")

    @property
    def operation_id(self) -> str:
        """Stable identity for retries, independent of the target generation."""

        return canonical_digest(
            JsonObject(
                {
                    "codex_session_id": self.codex_session_id,
                    "session_commitment": self.session_commitment,
                    "event_kind": self.event_kind,
                    "clear_mapping": self.clear_mapping,
                }
            )
        )


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
    # ``dedup`` remains a set for compatibility with older state and callers,
    # while this insertion order plus lane map makes bounded eviction
    # deterministic and session-aware.  The metadata is additive and can be
    # reconstructed from retained envelopes for legacy state.
    dedup_order: list[str] | None = None
    dedup_lanes: dict[str, str] | None = None
    envelopes: list[ObservationEnvelope] | None = None
    gaps: dict[str, _GapState] | None = None
    # Active retention gaps keyed by the affected session commitment.  The
    # ordinary workspace gap history remains the aggregate compatibility view;
    # this map prevents a busy session's bounded loss from being attributed to
    # every sibling.
    session_gaps: dict[str, set[str]] | None = None
    unsupported_events: set[str] | None = None
    last_receipt: Timestamp | None = None
    advice_frontier: str | None = None
    advice_snapshot: AdviceSnapshot | None = None
    last_advice_suppression: str | None = None
    session_advice: dict[str, AdviceSnapshot] | None = None
    session_advice_suppression: dict[str, str] | None = None
    frontier_motion_notices: dict[str, FrontierMotionNotice] | None = None
    # Last delivered frontier identity per Codex session. Survives notice
    # deletion so a replayed append cannot re-announce already-delivered motion.
    frontier_motion_delivered: dict[str, _FrontierMotionDelivered] | None = None
    frontier_motion_recency: int = 0
    open_pre: dict[str, str] | None = None
    stream_cursors: dict[str, ObservationCursor] | None = None
    stream_partials: dict[str, bytes] | None = None
    stream_call_tools: dict[str, dict[str, str]] | None = None
    stream_call_tool_generations: dict[str, int] | None = None
    stream_source_identities: dict[str, str] | None = None
    # Exact rollout profile id the current source generation's header admitted, per session.
    # Absent while a generation is unadmitted; a cursor with admitted events and no profile is
    # replayed from its header rather than parsed under a guessed vocabulary.
    stream_profiles: dict[str, str] | None = None
    stream_partial_dropped_sessions: set[str] | None = None
    hook_sequences: dict[str, int] | None = None
    # Workspace high-water mark for locally allocated hook ordinals.  The
    # per-session map is intentionally bounded, but an evicted active session
    # must never restart at one when it returns.
    hook_sequence_clock: int = 0
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
    pending_lifecycles: list[PendingSessionLifecycle] | None = None
    # A consent revoke is a two-store operation: the local fence is durable before the
    # service-owned project generations advance.  Keep the bounded token and generation plan
    # here so a daemon crash between those stores is retried before re-consent can clear it.
    pending_consent_revocation: str | None = None
    pending_consent_projects: dict[str, int] | None = None
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
        if self.dedup_order is None:
            self.dedup_order = []
        if self.dedup_lanes is None:
            self.dedup_lanes = {}
        if self.envelopes is None:
            self.envelopes = []
        if self.gaps is None:
            self.gaps = {}
        if self.session_gaps is None:
            self.session_gaps = {}
        if self.unsupported_events is None:
            self.unsupported_events = set()
        if self.open_pre is None:
            self.open_pre = {}
        if self.stream_cursors is None:
            self.stream_cursors = {}
        if self.stream_partials is None:
            self.stream_partials = {}
        if self.stream_call_tools is None:
            self.stream_call_tools = {}
        if self.stream_call_tool_generations is None:
            self.stream_call_tool_generations = {}
        if self.stream_source_identities is None:
            self.stream_source_identities = {}
        if self.stream_profiles is None:
            self.stream_profiles = {}
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
        if self.pending_lifecycles is None:
            self.pending_lifecycles = []
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
                cast(int, notice.get("recency_ordinal", 0)),
            )
        except ProtocolValueError, TypeError, ValueError:
            continue
    return result


def _load_frontier_motion_delivered(raw: object) -> dict[str, _FrontierMotionDelivered]:
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, _FrontierMotionDelivered] = {}
    for key, value in cast(Mapping[str, JsonValue], raw).items():
        if type(key) is not str or not isinstance(value, Mapping):
            continue
        entry = cast(Mapping[str, JsonValue], value)
        try:
            # Marks written before head/recency identity existed are ignored.
            # They cannot distinguish a replay from a same-task rewind, so the
            # only honest upgrade behavior is to fail open to a duplicate.
            result[key] = _FrontierMotionDelivered(
                cast(str, entry.get("task_id")),
                cast(int, entry.get("to_sequence")),
                cast(str, entry.get("head_digest")),
                cast(int, entry.get("recency_ordinal")),
            )
        except ProtocolValueError, TypeError, ValueError:
            continue
    return result


def _renumber_frontier_motion_recency(state: _WorkspaceState) -> None:
    """Compact the bounded LRU clock before its canonical integer can overflow."""

    notices = state.frontier_motion_notices or {}
    delivered = state.frontier_motion_delivered or {}
    ordered = sorted(
        (
            *((notice.recency_ordinal, 0, session) for session, notice in notices.items()),
            *((mark.recency_ordinal, 1, session) for session, mark in delivered.items()),
        ),
        key=lambda item: (item[0], item[1], item[2].encode()),
    )
    for ordinal, (_prior, kind, session) in enumerate(ordered, 1):
        if kind == 0:
            notices[session] = dataclasses.replace(notices[session], recency_ordinal=ordinal)
        else:
            delivered[session] = dataclasses.replace(delivered[session], recency_ordinal=ordinal)
    state.frontier_motion_recency = len(ordered)


def _next_frontier_motion_recency(state: _WorkspaceState) -> int:
    if state.frontier_motion_recency >= _MAX_SAFE_INTEGER:
        _renumber_frontier_motion_recency(state)
    state.frontier_motion_recency += 1
    return state.frontier_motion_recency


def _touch_frontier_motion_notice(
    state: _WorkspaceState,
    session_id: str,
    notice: FrontierMotionNotice,
) -> None:
    assert state.frontier_motion_notices is not None
    state.frontier_motion_notices[session_id] = dataclasses.replace(
        notice,
        recency_ordinal=_next_frontier_motion_recency(state),
    )


def _touch_frontier_motion_delivered(
    state: _WorkspaceState,
    session_id: str,
    *,
    task_id: str,
    to_sequence: int,
    head_digest: str,
) -> _FrontierMotionDelivered:
    assert state.frontier_motion_delivered is not None
    mark = _FrontierMotionDelivered(
        task_id,
        to_sequence,
        head_digest,
        _next_frontier_motion_recency(state),
    )
    state.frontier_motion_delivered[session_id] = mark
    return mark


def _frontier_lineage_rewound(
    *,
    current_sequence: int,
    current_head_digest: str,
    recorded_sequence: int,
    recorded_head_digest: str,
) -> bool:
    """Return whether the actual routed head proves a stored frontier is gone."""

    return current_sequence < recorded_sequence or (
        current_sequence == recorded_sequence and current_head_digest != recorded_head_digest
    )


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
        dedup_order=list(state.dedup_order or ()),
        dedup_lanes=dict(state.dedup_lanes or {}),
        envelopes=list(state.envelopes or ()),
        gaps=dict(state.gaps or {}),
        session_gaps={session: set(gaps) for session, gaps in (state.session_gaps or {}).items()},
        unsupported_events=set(state.unsupported_events or ()),
        session_advice=dict(state.session_advice or {}),
        session_advice_suppression=dict(state.session_advice_suppression or {}),
        frontier_motion_notices=dict(state.frontier_motion_notices or {}),
        frontier_motion_delivered=dict(state.frontier_motion_delivered or {}),
        open_pre=dict(state.open_pre or {}),
        stream_cursors=dict(state.stream_cursors or {}),
        stream_partials=dict(state.stream_partials or {}),
        stream_call_tools={
            session: dict(tools) for session, tools in (state.stream_call_tools or {}).items()
        },
        stream_call_tool_generations=dict(state.stream_call_tool_generations or {}),
        stream_source_identities=dict(state.stream_source_identities or {}),
        stream_profiles=dict(state.stream_profiles or {}),
        stream_partial_dropped_sessions=set(state.stream_partial_dropped_sessions or ()),
        hook_sequences=dict(state.hook_sequences or {}),
        hook_sequence_clock=state.hook_sequence_clock,
        pending_outbox=list(state.pending_outbox or ()),
        quarantine=list(state.quarantine or ()),
        codex_session_bindings=dict(state.codex_session_bindings or {}),
        storage_corrupt_sessions=set(state.storage_corrupt_sessions or ()),
        ended_sessions=set(state.ended_sessions or ()),
        session_generations=dict(state.session_generations or {}),
        ended_session_generations=dict(state.ended_session_generations or {}),
        pending_lifecycles=list(state.pending_lifecycles or ()),
        pending_consent_revocation=state.pending_consent_revocation,
        pending_consent_projects=(
            None if state.pending_consent_projects is None else dict(state.pending_consent_projects)
        ),
    )


def _stream_profile_id_valid(profile_id: object) -> bool:
    """Accept ``None`` or one bounded ASCII profile-id token such as ``codex-rollout-jsonl/x/v1``."""

    if profile_id is None:
        return True
    return (
        type(profile_id) is str
        and 0 < len(profile_id) <= 128
        and profile_id.isascii()
        and profile_id[0].isalnum()
        and all(char.isalnum() or char in "._+/-" for char in profile_id)
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
            if state.pending_consent_revocation is not None:
                # A project-generation fence is still pending in the service-owned catalog.
                # Re-consent must wait for that fence; otherwise the old detection generation
                # could become eligible again after a process crash (#502).
                raise _error(
                    PublicErrorCode.SESSION_CONFLICT,
                    "Observation consent revocation is still being fenced.",
                    retryable=True,
                )
            stamp = granted_at if granted_at is not None else _now()
            state.consent = LocalObservationConsent(
                workspace_commitment=workspace_commitment,
                granted_at=stamp,
                revoked_at=None,
                paused=False,
            )
            state.pending_consent_revocation = None
            state.pending_consent_projects = None
            self._save(workspace_commitment, state)

    def pending_consent_revocation(
        self, workspace_commitment: str
    ) -> tuple[str, tuple[tuple[str, int], ...] | None] | None:
        """Return the durable source-consent fence awaiting project invalidation.

        The token and generation plan contain only structural commitments.  They are kept out of
        ordinary status output, but survive a daemon restart so a revoked source cannot be
        re-consented while its old project generation is still queued for delivery.
        """

        with self._lock:
            state = self._load(workspace_commitment)
            token = state.pending_consent_revocation
            if token is None:
                return None
            if state.pending_consent_projects is None:
                return token, None
            return token, tuple(
                sorted(state.pending_consent_projects.items(), key=lambda item: item[0].encode())
            )

    def record_consent_revocation_plan(
        self,
        workspace_commitment: str,
        token: str,
        project_generations: Mapping[str, int],
    ) -> None:
        """Durably bind one revoke token to the project generations it must fence."""

        validate_sha256_digest(token)
        normalized: dict[str, int] = {}
        if len(project_generations) > _MAX_PENDING_CONSENT_PROJECTS:
            raise ProtocolValueError("invalid_event_value_type")
        for project_id, generation in project_generations.items():
            validate_id(IdKind.PROJECT, project_id)
            if type(generation) is not int or isinstance(generation, bool) or generation < 1:
                raise ProtocolValueError("invalid_event_value_type")
            normalized[project_id] = generation
        with self._lock:
            state = self._load(workspace_commitment)
            if state.pending_consent_revocation != token:
                raise _error(
                    PublicErrorCode.SESSION_CONFLICT,
                    "Observation consent revocation is no longer current.",
                    retryable=False,
                )
            if (
                state.pending_consent_projects is not None
                and state.pending_consent_projects != normalized
            ):
                raise _error(
                    PublicErrorCode.SESSION_CONFLICT,
                    "Observation consent revocation plan changed.",
                    retryable=False,
                )
            state.pending_consent_projects = normalized
            self._save(workspace_commitment, state)

    def mark_consent_revocation_fenced(self, workspace_commitment: str, token: str) -> None:
        """Clear a completed project-generation fence, idempotently."""

        validate_sha256_digest(token)
        with self._lock:
            state = self._load(workspace_commitment)
            if state.pending_consent_revocation != token:
                return
            state.pending_consent_revocation = None
            state.pending_consent_projects = None
            self._save(workspace_commitment, state)

    def _session_workspace_owners_unlocked(self, session_commitment: str) -> frozenset[str]:
        """Return every local workspace that records one session commitment.

        ``session_workspaces`` is the envelope routing index while
        ``codex_session_bindings`` is the raw host-session index.  They are
        written by separate compatibility paths, so ownership checks must
        consult both maps while the interprocess store lock is held.  A
        disagreement between the containing workspace and a stored route is
        treated as two owners and therefore fails closed.
        """

        owners: set[str] = set()
        for workspace, state in self._iter_workspaces():
            assert state.session_workspaces is not None
            bound_workspace = state.session_workspaces.get(session_commitment)
            if bound_workspace is not None:
                owners.add(workspace)
                if type(bound_workspace) is str and bound_workspace:
                    owners.add(bound_workspace)
            assert state.codex_session_bindings is not None
            if session_commitment in state.codex_session_bindings.values():
                owners.add(workspace)
            # A pruned ended binding retains its generation tombstone so late
            # replay and a same-workspace reattach keep their identity. Treat
            # that tombstone as ownership evidence too; otherwise the same raw
            # host session could be rebound to a different workspace after GC.
            if session_commitment in (state.session_generations or {}):
                owners.add(workspace)
            if session_commitment in (state.ended_session_generations or {}):
                owners.add(workspace)
        return frozenset(owners)

    def bind_session(self, workspace_commitment: str, session_commitment: str) -> None:
        with self._lock:
            state = self._load(workspace_commitment)
            if state.consent is None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is missing.",
                    retryable=False,
                )
            owners = self._session_workspace_owners_unlocked(session_commitment)
            if owners - {workspace_commitment}:
                raise _error(
                    PublicErrorCode.SESSION_CONFLICT,
                    "Observation session is already bound.",
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
            generation = self._begin_session_generation_state(state, session_commitment)
            self._save(workspace_commitment, state)
            return generation

    @staticmethod
    def _begin_session_generation_state(state: _WorkspaceState, session_commitment: str) -> int:
        """Advance one generation in an already-held workspace state."""

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
            LocalObservationStore._resolve_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
        return generation

    def current_session_generation(self, workspace_commitment: str, session_commitment: str) -> int:
        with self._lock:
            state = self._load(workspace_commitment)
            assert state.session_generations is not None
            return state.session_generations.get(session_commitment, 0) or 1

    def persisted_session_generation(
        self, workspace_commitment: str, session_commitment: str
    ) -> int:
        """Return the stored counter, preserving zero for legacy state.

        ``current_session_generation`` is the public event-stamping view and
        therefore presents a pre-counter binding as generation one. Lifecycle
        reconciliation needs the persisted counter itself so a deferred first
        start can materialize generation one exactly once instead of advancing
        a legacy ended binding to generation two.
        """

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.session_generations is not None
            return state.session_generations.get(session_commitment, 0)

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
            if self._note_session_end_state(
                state, workspace_commitment, session_commitment, generation
            ):
                self._save(workspace_commitment, state)

    @staticmethod
    def _note_session_end_state(
        state: _WorkspaceState,
        workspace_commitment: str,
        session_commitment: str,
        generation: int | None,
    ) -> bool:
        """Apply one generation-fenced end to an already-held workspace state."""

        assert state.ended_sessions is not None
        assert state.session_generations is not None
        assert state.ended_session_generations is not None
        assert state.session_workspaces is not None
        current = state.session_generations.get(session_commitment, 1)
        observed = current if generation is None else generation
        if observed != current:
            return False
        # Retain the binding so "all bound sessions ended" is computable.
        state.session_workspaces.setdefault(session_commitment, workspace_commitment)
        state.ended_sessions.add(session_commitment)
        state.ended_session_generations[session_commitment] = observed
        assert state.stream_partial_dropped_sessions is not None
        state.stream_partial_dropped_sessions.discard(session_commitment)
        if not state.stream_partial_dropped_sessions:
            LocalObservationStore._resolve_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
        return True

    def bind_codex_session(self, workspace_commitment: str, codex_session_id: str) -> str:
        """Bind a Codex session id to a consented workspace; return session commitment."""

        session = self.session_commitment(codex_session_id)
        with self._lock:
            state = self._load(workspace_commitment)
            if state.consent is None:
                raise _error(
                    PublicErrorCode.INVALID_REQUEST,
                    "Observation consent is missing.",
                    retryable=False,
                )
            owners = self._session_workspace_owners_unlocked(session)
            if owners - {workspace_commitment}:
                raise _error(
                    PublicErrorCode.SESSION_CONFLICT,
                    "Observation session is already bound.",
                    retryable=False,
                )
            assert state.session_workspaces is not None
            state.session_workspaces[session] = workspace_commitment
            assert state.codex_session_bindings is not None
            state.codex_session_bindings[codex_session_id] = session
            self._save(workspace_commitment, state)
        return session

    def reconcile_outbox_session_lifecycle(
        self,
        workspace_commitment: str,
        row: ObservationOutboxRow,
        *,
        session_lock_owned: bool = False,
    ) -> bool:
        """Converge raw session membership before one outbox row is delivered.

        A hook can capture an envelope while the workspace reservation is held by
        recovery. The row is the durable handoff containing both the raw host
        session id and its target workspace. A later hook drain or READY sweep
        binds that id under the same workspace-then-session locks before sending
        the row. Contended locks and foreign ownership return ``False`` so the
        caller leaves the row untouched for a later pass.
        """

        if type(row) is not ObservationOutboxRow:
            return False
        pending = self.list_pending_session_lifecycles(workspace_commitment, row.codex_session_id)
        if not pending and row.codex_session_id in self.codex_sessions_for_workspace(
            workspace_commitment
        ):
            # The normal mapped-session path already serialized its lifecycle
            # mutation before enqueueing. Avoid taking a second pair of locks
            # for every ordinary delivery row.
            return True
        if not self.reconcile_pending_session_lifecycles(
            workspace_commitment,
            row.codex_session_id,
            session_lock_owned=session_lock_owned,
        ):
            return False
        if self.list_pending_session_lifecycles(workspace_commitment, row.codex_session_id):
            # A foreign owner or stale target remains unresolved. Do not route
            # the row before its explicit lifecycle intent converges.
            return False
        from yoetz.adapters.integrations.codex_lifecycle import (
            acquire_session_lock,
            acquire_workspace_recovery_lock,
        )

        try:
            with acquire_workspace_recovery_lock(
                workspace_commitment, _state=self._state_root
            ) as workspace_owned:
                if not workspace_owned:
                    return False
                session_lock = (
                    contextlib.nullcontext(True)
                    if session_lock_owned
                    else acquire_session_lock(row.codex_session_id, _state=self._state_root)
                )
                with session_lock as session_owned:
                    if not session_owned:
                        return False
                    session_commitment = self.session_commitment(row.codex_session_id)
                    known = row.codex_session_id in self.codex_sessions_for_workspace(
                        workspace_commitment
                    )
                    if not known:
                        self.bind_codex_session(workspace_commitment, row.codex_session_id)
                    current_generation = self.current_session_generation(
                        workspace_commitment, session_commitment
                    )
                    event_kind = row.envelope.event_kind
                    source_generation = row.envelope.cursor.source_generation
                    if event_kind == "SessionEnd" and (current_generation == source_generation):
                        self.note_session_end(
                            workspace_commitment,
                            session_commitment,
                            generation=source_generation,
                        )
                    elif event_kind == "SessionStart":
                        # A historical outbox Start does not prove that its
                        # lifecycle was deferred. Only explicit pending
                        # intents may advance a generation.
                        return True
                    return True
        except Exception:
            return False

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
            owners = self._session_workspace_owners_unlocked(
                self.session_commitment(codex_session_id)
            )
            if len(owners) == 1:
                workspace = next(iter(owners))
                consent = self._load(workspace).consent
                if consent is not None and consent.active:
                    return workspace
                return None
            if owners:
                # A duplicated raw binding is a local ownership conflict. Never
                # choose the first filesystem entry as an implicit selector.
                return None
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

    def unambiguous_codex_sessions_for_workspace(
        self, workspace_commitment: str
    ) -> tuple[str, ...]:
        """Return host session IDs recorded in this workspace and no other local workspace."""

        with self._lock:
            target = self._load(workspace_commitment)
            assert target.codex_session_bindings is not None
            return tuple(
                sorted(
                    (
                        session_id
                        for session_id, session in target.codex_session_bindings.items()
                        if self._session_workspace_owners_unlocked(session)
                        == frozenset((workspace_commitment,))
                    ),
                    key=str.encode,
                )
            )

    def codex_session_lifecycles_for_workspace(
        self, workspace_commitment: str
    ) -> tuple[tuple[str, bool], ...]:
        """Return every bound host session id with its ended flag from one state read.

        The SessionStart recovery scan needs the ended flag of every binding in the
        workspace. Asking ``codex_session_ended`` per binding copies the whole state once
        per call outside a batch, so the scan cost grew with the binding count (#549).
        """

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.codex_session_bindings is not None
            assert state.ended_sessions is not None
            bindings = state.codex_session_bindings
            ended = state.ended_sessions
            return tuple(
                (session_id, bindings[session_id] in ended)
                for session_id in sorted(bindings, key=str.encode)
            )

    def prune_codex_session_bindings(
        self, workspace_commitment: str, codex_session_ids: Iterable[str]
    ) -> tuple[str, ...]:
        """Drop the requested bindings whose sessions are ended and fully drained (#549).

        Rule: a binding is removed only when its session is marked ended for its current
        generation, no pending outbox row and no quarantine row still names the session,
        and the session is not held as storage-corrupt. Anything else is kept whatever
        the caller asked for. The ended-unmapped quarantine path resolves a host session
        id through this map to decide that its rows are terminal, and the corruption
        repair path clears a session through it, so a binding with undrained work must
        outlive that work. A pruned session that resumes re-binds on its next hook event;
        its generation counter is keyed by commitment and retained, so the resumed
        generation continues rather than restarting. Returns the ids removed, sorted.
        """

        requested = {
            session_id for session_id in codex_session_ids if type(session_id) is str and session_id
        }
        if not requested:
            return ()
        with self._lock:
            state = self._load(workspace_commitment)
            assert state.codex_session_bindings is not None
            assert state.ended_sessions is not None
            assert state.pending_outbox is not None
            assert state.quarantine is not None
            assert state.storage_corrupt_sessions is not None
            assert state.pending_lifecycles is not None
            bindings = state.codex_session_bindings
            busy = {row.codex_session_id for row in state.pending_outbox}
            busy.update(entry[0] for entry in state.quarantine)
            busy.update(state.storage_corrupt_sessions)
            busy.update(intent.codex_session_id for intent in state.pending_lifecycles)
            removed: list[str] = []
            for session_id in sorted(requested & bindings.keys(), key=str.encode):
                if bindings[session_id] not in state.ended_sessions or session_id in busy:
                    continue
                session_commitment = bindings[session_id]
                del bindings[session_id]
                # One-shot frontier notices are keyed by host session id and are
                # only ever dropped through the binding's ended flag.
                if state.frontier_motion_notices:
                    state.frontier_motion_notices.pop(session_id, None)
                if state.frontier_motion_delivered:
                    state.frontier_motion_delivered.pop(session_id, None)
                # The workspace route has no work left to resolve once the raw
                # binding is removed. Keep the ended generation fence and all
                # replay state (cursors, stream identity/profile, dedup, and
                # hook high-water) so a later reattach resumes monotonically;
                # ``ended_sessions`` remains the stopped-state fence that is
                # cleared only by the next SessionStart generation.
                if not any(bound == session_commitment for bound in bindings.values()):
                    assert state.session_generations is not None
                    assert state.session_workspaces is not None
                    state.session_generations.setdefault(session_commitment, 0)
                    state.session_workspaces.pop(session_commitment, None)
                    if state.stream_partial_dropped_sessions is not None:
                        state.stream_partial_dropped_sessions.discard(session_commitment)
                        if not state.stream_partial_dropped_sessions:
                            self._resolve_gap_state(state, _LOCAL_STREAM_PARTIAL_DROPPED_GAP)
                removed.append(session_id)
            if removed:
                self._save(workspace_commitment, state)
            return tuple(removed)

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
        lineage_frontier: Frontier | None = None,
    ) -> None:
        """Merge contiguous undelivered appends against the actual routed lineage."""

        candidate = FrontierMotionNotice(
            from_sequence,
            to_sequence,
            head_digest,
            observation_record_count,
            task_id,
        )
        if lineage_frontier is None:
            # Compatibility for direct local-store callers. The application
            # coordinator always supplies the actual routed head so a replayed
            # operation result cannot masquerade as the current lineage.
            lineage_frontier = Frontier(candidate.to_sequence, candidate.head_digest)
        if (
            type(lineage_frontier) is not Frontier
            or lineage_frontier.sequence < candidate.to_sequence
        ):
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            assert state.frontier_motion_notices is not None
            assert state.frontier_motion_delivered is not None
            notices = state.frontier_motion_notices
            delivered = state.frontier_motion_delivered
            mutated = False
            # The delivered mark is scoped to one task and one observed ledger
            # lineage. The actual current routed frontier distinguishes a
            # historical completed-operation replay (current head still at or
            # beyond the mark) from a same-task restore whose head fell behind
            # or diverged at the marked sequence.
            delivered_entry = delivered.get(codex_session_id)
            if delivered_entry is not None and delivered_entry.task_id != task_id:
                del delivered[codex_session_id]
                delivered_entry = None
                mutated = True
            elif delivered_entry is not None and _frontier_lineage_rewound(
                current_sequence=lineage_frontier.sequence,
                current_head_digest=lineage_frontier.head_digest,
                recorded_sequence=delivered_entry.to_sequence,
                recorded_head_digest=delivered_entry.head_digest,
            ):
                del delivered[codex_session_id]
                delivered_entry = None
                mutated = True
            elif delivered_entry is not None:
                delivered_entry = _touch_frontier_motion_delivered(
                    state,
                    codex_session_id,
                    task_id=delivered_entry.task_id,
                    to_sequence=delivered_entry.to_sequence,
                    head_digest=delivered_entry.head_digest,
                )
                mutated = True
            delivered_to = delivered_entry.to_sequence if delivered_entry is not None else 0
            prior = notices.get(codex_session_id)
            if prior is not None and prior.task_id != task_id:
                del notices[codex_session_id]
                prior = None
                mutated = True
            elif prior is not None and _frontier_lineage_rewound(
                current_sequence=lineage_frontier.sequence,
                current_head_digest=lineage_frontier.head_digest,
                recorded_sequence=prior.to_sequence,
                recorded_head_digest=prior.head_digest,
            ):
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
            _touch_frontier_motion_notice(state, codex_session_id, clamped)
            self._save(workspace, state)

    def peek_frontier_motion(
        self, workspace: str, codex_session_id: str
    ) -> FrontierMotionNotice | None:
        with self._lock:
            state = self._load(workspace)
            return (state.frontier_motion_notices or {}).get(codex_session_id)

    def commit_frontier_motion_delivery(
        self,
        workspace: str,
        codex_session_id: str,
        delivery_identity: str,
        *,
        emitted_to_sequence: int | None = None,
        emitted_task_id: str | None = None,
        emitted_head_digest: str | None = None,
    ) -> None:
        """Advance the delivered mark for the notice bytes that reached the hook consumer.

        Identity match removes that exact notice. A merge that races peek and commit
        changes identity; the peeked emitted frontier still advances high-water and
        clamps any same-task remainder so the emitted range is not re-announced.
        A queued same-task notice below the emitted frontier proves the observed
        lineage rewound after the peek: the emitted mark is not reinstalled and the
        rewind notice stays queued so the new lineage's prefix is still announced.
        """

        with self._lock:
            state = self._load(workspace)
            notices = state.frontier_motion_notices or {}
            current = notices.get(codex_session_id)
            if current is None or current.delivery_identity != delivery_identity:
                if (
                    type(emitted_to_sequence) is not int
                    or type(emitted_task_id) is not str
                    or not emitted_task_id
                    or not 0 < emitted_to_sequence <= _MAX_SAFE_INTEGER
                    or type(emitted_head_digest) is not str
                ):
                    return
                try:
                    validate_sha256_digest(emitted_head_digest)
                except ProtocolValueError:
                    return
                if (
                    current is not None
                    and current.task_id == emitted_task_id
                    and _frontier_lineage_rewound(
                        current_sequence=current.to_sequence,
                        current_head_digest=current.head_digest,
                        recorded_sequence=emitted_to_sequence,
                        recorded_head_digest=emitted_head_digest,
                    )
                ):
                    # A same-task rewind was queued between peek and commit.
                    # Writing the emitted mark would clamp the rewind notice out
                    # of existence and silently drop the new lineage's prefix.
                    return
                assert state.frontier_motion_delivered is not None
                delivered = state.frontier_motion_delivered
                previous = delivered.pop(codex_session_id, None)
                if (
                    previous is not None
                    and previous.task_id == emitted_task_id
                    and previous.to_sequence > emitted_to_sequence
                ):
                    delivered_to = previous.to_sequence
                    delivered_head_digest = previous.head_digest
                else:
                    delivered_to = emitted_to_sequence
                    delivered_head_digest = emitted_head_digest
                _touch_frontier_motion_delivered(
                    state,
                    codex_session_id,
                    task_id=emitted_task_id,
                    to_sequence=delivered_to,
                    head_digest=delivered_head_digest,
                )
                if current is not None and current.task_id == emitted_task_id:
                    clamped = _clamp_frontier_motion_notice(current, delivered_to)
                    if clamped is None:
                        notices.pop(codex_session_id, None)
                    else:
                        _touch_frontier_motion_notice(state, codex_session_id, clamped)
                self._save(workspace, state)
                return
            del notices[codex_session_id]
            assert state.frontier_motion_delivered is not None
            delivered = state.frontier_motion_delivered
            previous = delivered.pop(codex_session_id, None)
            if (
                previous is not None
                and previous.task_id == current.task_id
                and previous.to_sequence > current.to_sequence
            ):
                delivered_to = previous.to_sequence
                delivered_head_digest = previous.head_digest
            else:
                delivered_to = current.to_sequence
                delivered_head_digest = current.head_digest
            _touch_frontier_motion_delivered(
                state,
                codex_session_id,
                task_id=current.task_id,
                to_sequence=delivered_to,
                head_digest=delivered_head_digest,
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

    def session_gap_codes(self, workspace: str, session_commitment: str) -> tuple[str, ...]:
        """Return active bounded-loss codes for one session lane.

        Workspace status keeps its historical aggregate gap vocabulary for
        compatibility. This scoped view lets a caller explain which sibling
        lane encountered retention pressure without attributing that loss to
        every session sharing the workspace.
        """

        with self._lock:
            state = self._load(workspace)
            assert state.session_gaps is not None
            return tuple(sorted(state.session_gaps.get(session_commitment, ()), key=str.encode))

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

    def stream_call_tools_for_session(
        self,
        workspace: str,
        session_commitment: str,
        *,
        source_generation: int,
    ) -> dict[str, str]:
        with self._lock:
            state = self._load(workspace)
            assert state.stream_call_tools is not None
            assert state.stream_call_tool_generations is not None
            if state.stream_call_tool_generations.get(session_commitment) != source_generation:
                return {}
            return dict(state.stream_call_tools.get(session_commitment, {}))

    def replace_stream_call_tools(
        self,
        workspace: str,
        session_commitment: str,
        *,
        source_generation: int,
        call_tools: Mapping[str, str],
    ) -> None:
        if type(source_generation) is not int or source_generation < 1:
            raise ProtocolValueError("invalid_event_value_type")
        clean: dict[str, str] = {}
        for call_id, tool_name in call_tools.items():
            if (
                type(call_id) is not str
                or type(tool_name) is not str
                or not call_id
                or not tool_name
                or len(call_id) > 128
                or len(tool_name) > 128
            ):
                raise ProtocolValueError("invalid_event_value_type")
            if len(clean) >= _MAX_STREAM_CALL_TOOLS:
                break
            clean[call_id] = tool_name
        with self._lock:
            state = self._load(workspace)
            assert state.stream_call_tools is not None
            assert state.stream_call_tool_generations is not None
            state.stream_call_tools.pop(session_commitment, None)
            state.stream_call_tool_generations.pop(session_commitment, None)
            retained = sum(len(tools) for tools in state.stream_call_tools.values())
            while retained + len(clean) > _MAX_STREAM_CALL_TOOLS and state.stream_call_tools:
                oldest_session = next(iter(state.stream_call_tools))
                oldest_tools = state.stream_call_tools[oldest_session]
                oldest_tools.pop(next(iter(oldest_tools)))
                retained -= 1
                if not oldest_tools:
                    del state.stream_call_tools[oldest_session]
                    state.stream_call_tool_generations.pop(oldest_session, None)
            if clean:
                state.stream_call_tools[session_commitment] = clean
                state.stream_call_tool_generations[session_commitment] = source_generation
            self._save(workspace, state)

    def stream_source_identity_for_session(
        self, workspace: str, session_commitment: str
    ) -> str | None:
        with self._lock:
            state = self._load(workspace)
            assert state.stream_source_identities is not None
            return state.stream_source_identities.get(session_commitment)

    def set_stream_source_identity(
        self,
        workspace: str,
        session_commitment: str,
        source_identity: str | None,
    ) -> None:
        if source_identity is not None and (
            type(source_identity) is not str
            or not source_identity.startswith("hmac-sha256:")
            or len(source_identity) != 76
        ):
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            assert state.stream_source_identities is not None
            if source_identity is None:
                state.stream_source_identities.pop(session_commitment, None)
            else:
                state.stream_source_identities[session_commitment] = source_identity
            self._save(workspace, state)

    def stream_profile_for_session(self, workspace: str, session_commitment: str) -> str | None:
        """Return the exact rollout profile id the session's current generation admitted."""

        with self._lock:
            state = self._load(workspace)
            assert state.stream_profiles is not None
            return state.stream_profiles.get(session_commitment)

    def set_stream_profile(
        self,
        workspace: str,
        session_commitment: str,
        profile_id: str | None,
    ) -> None:
        if not _stream_profile_id_valid(profile_id):
            raise ProtocolValueError("invalid_event_value_type")
        with self._lock:
            state = self._load(workspace)
            assert state.stream_profiles is not None
            if profile_id is None:
                state.stream_profiles.pop(session_commitment, None)
            else:
                state.stream_profiles[session_commitment] = profile_id
            self._save(workspace, state)

    def set_stream_reconcile_state(
        self,
        workspace: str,
        session_commitment: str,
        *,
        cursor: ObservationCursor,
        partial: bytes,
        call_tools: Mapping[str, str],
        source_identity: str | None,
        profile_id: str | None = None,
    ) -> None:
        """Atomically persist one replay-safe session-stream progress frontier."""

        if type(cursor) is not ObservationCursor or type(partial) is not bytes:
            raise ProtocolValueError("invalid_event_value_type")
        if source_identity is not None and (
            type(source_identity) is not str
            or not source_identity.startswith("hmac-sha256:")
            or len(source_identity) != 76
            or any(char not in "0123456789abcdef" for char in source_identity[12:])
        ):
            raise ProtocolValueError("invalid_event_value_type")
        if not _stream_profile_id_valid(profile_id):
            raise ProtocolValueError("invalid_event_value_type")
        clean: dict[str, str] = {}
        for call_id, tool_name in call_tools.items():
            if (
                type(call_id) is not str
                or type(tool_name) is not str
                or not call_id
                or not tool_name
                or len(call_id) > 128
                or len(tool_name) > 128
            ):
                raise ProtocolValueError("invalid_event_value_type")
            if len(clean) >= _MAX_STREAM_CALL_TOOLS:
                break
            clean[call_id] = tool_name
        with self._lock:
            state = self._load(workspace)
            assert state.stream_cursors is not None
            assert state.stream_partials is not None
            assert state.stream_partial_dropped_sessions is not None
            assert state.stream_call_tools is not None
            assert state.stream_call_tool_generations is not None
            assert state.stream_source_identities is not None
            assert state.stream_profiles is not None
            state.stream_cursors[session_commitment] = cursor
            if len(partial) > _MAX_STREAM_PARTIAL_BYTES:
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
            state.stream_call_tools.pop(session_commitment, None)
            state.stream_call_tool_generations.pop(session_commitment, None)
            retained = sum(len(tools) for tools in state.stream_call_tools.values())
            while retained + len(clean) > _MAX_STREAM_CALL_TOOLS and state.stream_call_tools:
                oldest_session = next(iter(state.stream_call_tools))
                oldest_tools = state.stream_call_tools[oldest_session]
                oldest_tools.pop(next(iter(oldest_tools)))
                retained -= 1
                if not oldest_tools:
                    del state.stream_call_tools[oldest_session]
                    state.stream_call_tool_generations.pop(oldest_session, None)
            if clean:
                state.stream_call_tools[session_commitment] = clean
                state.stream_call_tool_generations[session_commitment] = cursor.source_generation
            if source_identity is None:
                state.stream_source_identities.pop(session_commitment, None)
            else:
                state.stream_source_identities[session_commitment] = source_identity
            if profile_id is None:
                state.stream_profiles.pop(session_commitment, None)
            else:
                state.stream_profiles[session_commitment] = profile_id
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
            hook_sequences = state.hook_sequences
            previous = hook_sequences.get(session_commitment, 0)
            state.hook_sequence_clock = max(
                state.hook_sequence_clock,
                previous,
                max(hook_sequences.values(), default=0),
            )
            next_value = min(_MAX_SAFE_INTEGER, state.hook_sequence_clock + 1)
            state.hook_sequence_clock = next_value
            # Re-touch the session so the bounded map evicts by recency rather
            # than by first-ever admission. The workspace clock preserves
            # monotonicity even when an active session is eventually evicted.
            hook_sequences.pop(session_commitment, None)
            hook_sequences[session_commitment] = next_value
            # Bound retained sequence keys.
            if len(hook_sequences) > _MAX_HOOK_SEQUENCES:
                active = set(state.session_workspaces or ()) - set(state.ended_sessions or ())
                active.update(
                    session
                    for session in (state.codex_session_bindings or {}).values()
                    if session not in (state.ended_sessions or set())
                )
                candidates = [
                    session
                    for session in hook_sequences
                    if session != session_commitment and session not in active
                ]
                if not candidates:
                    candidates = [
                        session for session in hook_sequences if session != session_commitment
                    ]
                if candidates:
                    # Dict insertion order is not a durable recency signal:
                    # state serialization canonicalizes map keys, so a
                    # restart would otherwise evict by lexical key order.
                    # Locally allocated ordinals are monotonic and therefore
                    # provide a stable recency fence across reloads.
                    oldest = min(
                        candidates,
                        key=lambda session: (
                            hook_sequences.get(session, 0),
                            session.encode(),
                        ),
                    )
                    del hook_sequences[oldest]
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
            assert state.session_gaps is not None
            # A retry of an already-persisted row is idempotent even when the
            # aggregate queue is full. Capacity arbitration must never mutate
            # or report a gap for a duplicate replay.
            incoming_key = _dedup_key(workspace, envelope)
            for row in state.pending_outbox:
                if (
                    row.codex_session_id == codex_session_id
                    and _dedup_key(workspace, row.envelope) == incoming_key
                ):
                    return None
            if len(state.pending_outbox) >= _MAX_OUTBOX:
                # Preserve strict FIFO for an existing lane. If a new session
                # arrives while one sibling has consumed the whole aggregate
                # bound, reclaim one overrepresented row into quarantine so
                # the new lane gets a durable slot. The quarantine and scoped
                # gap make the reclaimed row explicit rather than silently
                # dropping it.
                evicted_index = self._fair_new_lane_outbox_index(
                    state.pending_outbox, codex_session_id
                )
                if evicted_index is None:
                    self._note_gap_state(state, _LOCAL_OUTBOX_OVERFLOW_GAP)
                    self._note_session_gap_state(
                        state, envelope.session_commitment, ObservationGapCode.OUTBOX_OVERFLOW.value
                    )
                    self._save(workspace, state)
                    return ObservationGapCode.OUTBOX_OVERFLOW.value
                evicted_row = state.pending_outbox.pop(evicted_index)
                self._quarantine_row_state(
                    state,
                    evicted_row,
                    ObservationGapCode.OUTBOX_OVERFLOW.value,
                )
                self._note_gap_state(state, _LOCAL_OUTBOX_OVERFLOW_GAP)
                self._note_session_gap_state(
                    state,
                    evicted_row.envelope.session_commitment,
                    ObservationGapCode.OUTBOX_OVERFLOW.value,
                )
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
                self._note_session_gap_state(
                    state, envelope.session_commitment, ObservationGapCode.OUTBOX_OVERFLOW.value
                )
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
        """Return opaque commitments with undelivered rows or lifecycle work."""

        with self._lock:
            pending: list[str] = []
            for workspace, state in self._iter_workspaces():
                assert state.pending_outbox is not None
                assert state.pending_lifecycles is not None
                if state.pending_outbox or state.pending_lifecycles:
                    pending.append(workspace)
            return tuple(sorted(pending, key=str.encode))

    def list_pending_session_lifecycles(
        self, workspace_commitment: str, codex_session_id: str | None = None
    ) -> tuple[PendingSessionLifecycle, ...]:
        """Return immutable deferred lifecycle intents in capture order."""

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.pending_lifecycles is not None
            if codex_session_id is None:
                return tuple(state.pending_lifecycles)
            return tuple(
                intent
                for intent in state.pending_lifecycles
                if intent.codex_session_id == codex_session_id
            )

    def lifecycle_reconciliation_snapshot(
        self, workspace_commitment: str
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Return bound and deferred raw session ids from one state read."""

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.codex_session_bindings is not None
            assert state.pending_lifecycles is not None
            return (
                frozenset(state.codex_session_bindings),
                frozenset(intent.codex_session_id for intent in state.pending_lifecycles),
            )

    def effective_session_generation(
        self,
        workspace_commitment: str,
        codex_session_id: str,
        session_commitment: str,
    ) -> int:
        """Return the generation future envelopes must use while intents are queued."""

        with self._lock:
            state = self._load(workspace_commitment)
            assert state.session_generations is not None
            generation = state.session_generations.get(session_commitment, 0) or 1
            assert state.pending_lifecycles is not None
            for intent in state.pending_lifecycles:
                if (
                    intent.codex_session_id != codex_session_id
                    or intent.session_commitment != session_commitment
                ):
                    continue
                if intent.event_kind == "SessionStart":
                    generation = intent.target_generation
                elif intent.target_generation == generation:
                    # A queued end makes the next start a new generation.  The
                    # current generation remains the one stamped on events
                    # captured before that start is observed.
                    continue
            return generation

    def record_pending_session_lifecycle(
        self,
        workspace_commitment: str,
        codex_session_id: str,
        session_commitment: str,
        event_kind: str,
        target_generation: int,
        *,
        clear_mapping: bool = False,
    ) -> bool:
        """Persist one lifecycle intent when a nonblocking membership lock is busy.

        The intent and the envelope that caused it are committed by the same
        workspace batch in the hook. Consecutive duplicate intents collapse;
        distinct start/end transitions retain their order up to a bounded
        per-workspace queue.
        """

        intent = PendingSessionLifecycle(
            codex_session_id=codex_session_id,
            session_commitment=session_commitment,
            event_kind=event_kind,
            target_generation=target_generation,
            clear_mapping=clear_mapping,
        )
        with self._lock:
            state = self._load(workspace_commitment)
            assert state.pending_lifecycles is not None
            for prior in reversed(state.pending_lifecycles):
                if prior.codex_session_id != codex_session_id:
                    continue
                if (
                    prior.event_kind == intent.event_kind
                    and prior.target_generation == intent.target_generation
                    and prior.clear_mapping == intent.clear_mapping
                    and prior.session_commitment == intent.session_commitment
                ):
                    return True
                break
            if len(state.pending_lifecycles) >= _MAX_PENDING_LIFECYCLES:
                return False
            state.pending_lifecycles.append(intent)
            self._save(workspace_commitment, state)
            return True

    def _pending_session_workspace_owners(self, codex_session_id: str) -> frozenset[str]:
        """Return every raw binding owner while the store lock is held."""

        return self._session_workspace_owners_unlocked(self.session_commitment(codex_session_id))

    def _reconcile_pending_session_lifecycle_lanes(
        self,
        workspace_commitment: str,
        session_ids: tuple[str, ...],
        *,
        session_lock_owned: bool,
        owned_session_id: str | None,
    ) -> bool:
        """Reconcile each pending raw-session lane independently.

        A workspace reservation protects the shared state batch, while each
        session lock fences only that session's lifecycle.  One contended
        session therefore remains durable without preventing an unrelated
        session from converging in the same pass.
        """

        from yoetz.adapters.integrations.codex_lifecycle import (
            acquire_session_lock,
            acquire_workspace_recovery_lock,
            clear_mapping,
        )

        with acquire_workspace_recovery_lock(
            workspace_commitment, _state=self._state_root
        ) as workspace_owned:
            if not workspace_owned:
                return False
            acquired_any = False
            for session_id in session_ids:
                session_lock = (
                    contextlib.nullcontext(True)
                    if session_lock_owned and session_id == owned_session_id
                    else acquire_session_lock(session_id, _state=self._state_root)
                )
                with session_lock as session_owned:
                    if not session_owned:
                        continue
                    acquired_any = True
                    with self.batched(workspace_commitment):
                        state = self._load(workspace_commitment)
                        assert state.pending_lifecycles is not None
                        current = list(state.pending_lifecycles)
                        remaining: list[PendingSessionLifecycle] = []
                        changed = False
                        blocked = False
                        for intent in current:
                            if intent.codex_session_id != session_id:
                                remaining.append(intent)
                                continue
                            # Preserve FIFO for one raw session. If an older
                            # intent is still fenced, a later transition cannot
                            # be applied independently of it.
                            if blocked:
                                remaining.append(intent)
                                continue
                            owners = self._pending_session_workspace_owners(session_id)
                            if owners - {workspace_commitment}:
                                remaining.append(intent)
                                blocked = True
                                continue
                            if state.consent is None or not state.consent.active:
                                remaining.append(intent)
                                blocked = True
                                continue
                            assert state.codex_session_bindings is not None
                            assert state.session_workspaces is not None
                            assert state.session_generations is not None
                            assert state.ended_sessions is not None
                            session = self.session_commitment(intent.codex_session_id)
                            if session != intent.session_commitment:
                                remaining.append(intent)
                                blocked = True
                                continue
                            if intent.codex_session_id not in state.codex_session_bindings:
                                state.session_workspaces.setdefault(session, workspace_commitment)
                                state.codex_session_bindings[intent.codex_session_id] = session
                                changed = True
                            stored_generation = state.session_generations.get(session, 0)
                            # A legacy end may have been persisted before the
                            # generation counter existed; its public generation
                            # is still the initial value one. Starts keep the
                            # stored-zero distinction so a deferred first Start
                            # materializes generation one.
                            generation = (
                                stored_generation
                                if stored_generation > 0 or intent.event_kind == "SessionStart"
                                else 1
                            )
                            ended = session in state.ended_sessions
                            if intent.event_kind == "SessionStart":
                                if generation > intent.target_generation:
                                    # A stale clear has no authority over a later route.
                                    changed = True
                                    continue
                                if intent.clear_mapping:
                                    clear_mapping(intent.codex_session_id, _state=self._state_root)
                                if generation == intent.target_generation and not ended:
                                    # The operation reached its frozen target before
                                    # the worker got here. Do not increment again.
                                    pass
                                elif generation + 1 == intent.target_generation and (
                                    ended or intent.clear_mapping
                                ):
                                    self._begin_session_generation_state(state, session)
                                    changed = True
                                elif generation == 0 and intent.target_generation == 1:
                                    self._begin_session_generation_state(state, session)
                                    changed = True
                                elif generation > intent.target_generation:
                                    # A later generation already superseded this
                                    # stale intent; dropping it is idempotent.
                                    changed = True
                                else:
                                    remaining.append(intent)
                                    blocked = True
                                    continue
                            elif generation == intent.target_generation:
                                if not ended:
                                    self._note_session_end_state(
                                        state,
                                        workspace_commitment,
                                        session,
                                        intent.target_generation,
                                    )
                                    changed = True
                            elif generation < intent.target_generation:
                                remaining.append(intent)
                                blocked = True
                                continue
                            else:
                                # An older end is already superseded by a later
                                # generation and can be retired idempotently.
                                changed = True
                        if remaining != current:
                            state.pending_lifecycles[:] = remaining
                            changed = True
                        if changed:
                            self._save(workspace_commitment, state)
            return acquired_any

    def reconcile_pending_session_lifecycles(
        self,
        workspace_commitment: str,
        codex_session_id: str | None = None,
        *,
        session_lock_owned: bool = False,
    ) -> bool:
        """Apply queued lifecycle operations under the shared workspace reservation.

        ``False`` means a reservation or session lock was busy and the intents
        remain durable.  A successful pass removes only operations that were
        applied or proven already applied at their frozen target generation.
        """

        pending = self.list_pending_session_lifecycles(workspace_commitment, codex_session_id)
        if not pending:
            return True
        session_ids = tuple(sorted({intent.codex_session_id for intent in pending}, key=str.encode))
        return self._reconcile_pending_session_lifecycle_lanes(
            workspace_commitment,
            session_ids,
            session_lock_owned=session_lock_owned,
            owned_session_id=codex_session_id,
        )

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
                        consecutive_reason_attempts=(
                            min(row.consecutive_reason_attempts + 1, _MAX_SAFE_INTEGER)
                            if reason is not None and reason == row.last_reason
                            else (1 if reason is not None else 0)
                        ),
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
            if reason in _OBSERVATION_GAP_CODES:
                self._note_gap_state(state, reason)
                self._note_session_gap_state(state, moved.session_commitment, reason)
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            self._note_session_gap_state(
                state, moved.session_commitment, ObservationGapCode.OUTBOX_QUARANTINED.value
            )
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
            if reason in _OBSERVATION_GAP_CODES:
                self._note_gap_state(state, reason)
                self._note_session_gap_state(state, moved.session_commitment, reason)
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            self._note_session_gap_state(
                state, moved.session_commitment, ObservationGapCode.OUTBOX_QUARANTINED.value
            )
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
            if reason in _OBSERVATION_GAP_CODES:
                self._note_gap_state(state, reason)
                for row in moved:
                    self._note_session_gap_state(state, row.envelope.session_commitment, reason)
            if reason == ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value:
                state.storage_corrupt_sessions.add(codex_session_id)
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            for row in moved:
                self._note_session_gap_state(
                    state,
                    row.envelope.session_commitment,
                    ObservationGapCode.OUTBOX_QUARANTINED.value,
                )
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

    def note_session_coverage_gap(
        self, workspace: str, session_commitment: str, gap_code: str
    ) -> None:
        """Record a safe coverage gap for one session lane."""

        with self._lock:
            state = self._load(workspace)
            if (
                type(gap_code) is str
                and gap_code
                and type(session_commitment) is str
                and session_commitment
            ):
                self._note_session_gap_state(state, session_commitment, gap_code)
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

    @staticmethod
    def _note_session_gap_state(
        state: _WorkspaceState, session_commitment: str, gap_code: str
    ) -> None:
        """Retain an active bounded-loss marker for one observation lane."""

        assert state.session_gaps is not None
        if not session_commitment or not gap_code:
            return
        gaps: set[str] | None = state.session_gaps.get(session_commitment)
        if gaps is None:
            # Session commitments are already bounded by the envelope/session
            # admission paths. Keep this diagnostic map bounded independently
            # so malformed or legacy state cannot turn it into an unbounded
            # retention root.
            if len(state.session_gaps) >= _MAX_ENVELOPES:
                oldest = min(state.session_gaps, key=str.encode)
                del state.session_gaps[oldest]
            new_gaps: set[str] = set()
            state.session_gaps[session_commitment] = new_gaps
            gaps = new_gaps
        if gap_code not in gaps and len(gaps) >= _MAX_SESSION_GAP_CODES:
            return
        gaps.add(gap_code)

    @staticmethod
    def _resolve_session_gap_state(
        state: _WorkspaceState, gap_code: str, session_commitment: str | None = None
    ) -> None:
        assert state.session_gaps is not None
        targets = (
            (session_commitment,) if session_commitment is not None else tuple(state.session_gaps)
        )
        for session in targets:
            gaps = state.session_gaps.get(session)
            if gaps is None:
                continue
            gaps.discard(gap_code)
            if not gaps:
                del state.session_gaps[session]

    @staticmethod
    def _ordered_dedup_keys(state: _WorkspaceState) -> list[str]:
        """Return dedup keys in durable insertion order, repairing legacy state."""

        assert state.dedup is not None
        seen: set[str] = set()
        ordered: list[str] = []
        for key in state.dedup_order or ():
            if key in state.dedup and key not in seen:
                ordered.append(key)
                seen.add(key)
        # Older state only has the set. Appending its members in byte order is
        # deterministic and keeps the compatibility path from reintroducing
        # ``set.pop`` behaviour.
        ordered.extend(sorted((state.dedup - seen), key=str.encode))
        return ordered

    def _hydrate_dedup_metadata(self, workspace: str, state: _WorkspaceState) -> None:
        """Backfill lane metadata from retained envelopes when loading old state."""

        assert state.dedup_order is not None
        assert state.dedup_lanes is not None
        assert state.dedup is not None
        state.dedup_order[:] = self._ordered_dedup_keys(state)
        for key in tuple(state.dedup_lanes):
            if key not in state.dedup:
                del state.dedup_lanes[key]
        for envelope in state.envelopes or ():
            key = _dedup_key(workspace, envelope)
            if key in state.dedup and key not in state.dedup_lanes:
                state.dedup_lanes[key] = envelope.session_commitment

    @staticmethod
    def _select_dedup_eviction_key(state: _WorkspaceState) -> str | None:
        """Choose the oldest key from an overrepresented session lane."""

        assert state.dedup_lanes is not None
        ordered = LocalObservationStore._ordered_dedup_keys(state)
        if not ordered:
            return None
        counts: dict[str, int] = {}
        for key in ordered:
            # Unknown keys in pre-metadata state are treated as independent
            # lanes. That is conservative: it avoids evicting a known quiet
            # session merely because old keys have no recoverable attribution.
            lane = state.dedup_lanes.get(key, f"_unknown:{key}")
            counts[lane] = counts.get(lane, 0) + 1
        for key in ordered:
            lane = state.dedup_lanes.get(key, f"_unknown:{key}")
            if counts[lane] > 1:
                return key
        return ordered[0]

    @staticmethod
    def _fair_envelope_index(envelopes: list[ObservationEnvelope]) -> int | None:
        """Choose the oldest envelope from an overrepresented lane first."""

        if not envelopes:
            return None
        counts: dict[str, int] = {}
        for envelope in envelopes:
            counts[envelope.session_commitment] = counts.get(envelope.session_commitment, 0) + 1
        for index, envelope in enumerate(envelopes):
            if counts[envelope.session_commitment] > 1:
                return index
        # More sessions than the aggregate bound cannot all retain one row;
        # the oldest row is the deterministic, explicitly gap-marked fallback.
        return 0

    @staticmethod
    def _fair_outbox_index(rows: list[ObservationOutboxRow]) -> int | None:
        """Choose the oldest row from an overrepresented delivery lane."""

        if not rows:
            return None
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.codex_session_id] = counts.get(row.codex_session_id, 0) + 1
        for index, row in enumerate(rows):
            if counts[row.codex_session_id] > 1:
                return index
        return 0

    @staticmethod
    def _fair_new_lane_outbox_index(
        rows: list[ObservationOutboxRow], incoming_session: str
    ) -> int | None:
        """Return a safe eviction candidate only when a new lane needs a slot.

        Existing rows retain strict FIFO within their session. A full queue may
        therefore make an existing lane wait, while a new session can reclaim
        one row from an overrepresented sibling and preserve progress for both.
        """

        if any(row.codex_session_id == incoming_session for row in rows):
            return None
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.codex_session_id] = counts.get(row.codex_session_id, 0) + 1
        for index, row in enumerate(rows):
            if counts[row.codex_session_id] > 1:
                return index
        return None

    def _quarantine_row_state(
        self,
        state: _WorkspaceState,
        row: ObservationOutboxRow,
        reason: str,
        *,
        quarantined_at: Timestamp | None = None,
    ) -> bool:
        """Move one row into bounded quarantine while a state lock is held."""

        assert state.quarantine is not None
        already = any(
            entry[0] == row.codex_session_id
            and entry[1].source_identity == row.envelope.source_identity
            and observation_envelope_to_json(entry[1]) == observation_envelope_to_json(row.envelope)
            for entry in state.quarantine
        )
        if already:
            return False
        state.quarantine.append(
            (
                row.codex_session_id,
                row.envelope,
                reason,
                self._wall_timestamp() if quarantined_at is None else quarantined_at,
            )
        )
        while len(state.quarantine) > _MAX_QUARANTINE:
            evicted = state.quarantine.pop(0)
            self._record_quarantine_eviction(state, evicted[0], evicted[1], evicted[2])
        return True

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

    def ingest(
        self,
        envelope: ObservationEnvelope,
        *,
        workspace_commitment: str | None = None,
    ) -> ObservationIngestResult:
        """Durably ingest one envelope, optionally at an explicit local workspace.

        The explicit workspace is a hook-only fallback for an observation whose
        session membership could not be created because its nonblocking
        lifecycle reservation was busy. It selects the already-consented target
        state without mutating session ownership; ordinary callers continue to
        resolve the workspace from the envelope's bound session commitment.
        """

        if type(envelope) is not ObservationEnvelope:
            raise _error(
                PublicErrorCode.INVALID_REQUEST,
                "Observation envelope is invalid.",
                retryable=False,
            )
        with self._lock:
            if workspace_commitment is None:
                try:
                    workspace = self._workspace_for_envelope(envelope)
                except PublicOperationError:
                    return ObservationIngestResult(
                        ObservationIngestDisposition.REJECTED,
                        ObservationGapCode.CONSENT_MISSING.value,
                        None,
                    )
            else:
                try:
                    workspace = validate_commitment(workspace_commitment)
                except ProtocolValueError:
                    return ObservationIngestResult(
                        ObservationIngestDisposition.REJECTED,
                        ObservationGapCode.CONSENT_MISSING.value,
                        None,
                    )
                if self._session_workspace_owners_unlocked(envelope.session_commitment) - {
                    workspace
                }:
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
            if workspace_commitment is not None:
                assert state.session_workspaces is not None
                state.session_workspaces.setdefault(envelope.session_commitment, workspace)
            assert state.dedup is not None
            assert state.dedup_order is not None
            assert state.dedup_lanes is not None
            assert state.cursors is not None
            assert state.envelopes is not None
            assert state.gaps is not None
            assert state.unsupported_events is not None
            self._hydrate_dedup_metadata(workspace, state)
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
            state.dedup_order.append(key)
            state.dedup_lanes[key] = envelope.session_commitment
            while len(state.dedup_order) > _MAX_DEDUP:
                evicted_key = self._select_dedup_eviction_key(state)
                if evicted_key is None:
                    break
                state.dedup.discard(evicted_key)
                state.dedup_order.remove(evicted_key)
                evicted_session = state.dedup_lanes.pop(evicted_key, None)
                self._note_gap_state(state, _LOCAL_DEDUP_EVICTED_GAP)
                self._note_gap_state(state, ObservationGapCode.TRUNCATED_PAYLOAD.value)
                if evicted_session is not None:
                    self._note_session_gap_state(state, evicted_session, _LOCAL_DEDUP_EVICTED_GAP)
                    self._note_session_gap_state(
                        state, evicted_session, ObservationGapCode.TRUNCATED_PAYLOAD.value
                    )
            state.cursors[cursor_key] = envelope.cursor
            state.envelopes.append(envelope)
            while len(state.envelopes) > _MAX_ENVELOPES:
                evicted_index = self._fair_envelope_index(state.envelopes)
                if evicted_index is None:
                    break
                evicted = state.envelopes.pop(evicted_index)
                self._note_gap_state(state, ObservationGapCode.TRUNCATED_PAYLOAD.value)
                self._note_gap_state(state, _LOCAL_ENVELOPE_RETENTION_GAP)
                self._note_session_gap_state(
                    state,
                    evicted.session_commitment,
                    _LOCAL_ENVELOPE_RETENTION_GAP,
                )
                self._note_session_gap_state(
                    state,
                    evicted.session_commitment,
                    ObservationGapCode.TRUNCATED_PAYLOAD.value,
                )
            state.last_receipt = envelope.receipt_time
            mono_ms = int(self._now_mono() * 1000)
            state.monotonic_epoch = self._boot_epoch()
            if envelope.source in {
                ObservationSource.CLAUDE_HOOK,
                ObservationSource.CODEX_HOOK,
                ObservationSource.CURSOR_HOOK,
            }:
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
            if consent.revoked_at is None:
                token = canonical_digest(
                    JsonObject(
                        {
                            "workspace_commitment": consent.workspace_commitment,
                            "granted_at": consent.granted_at.wire,
                            "revoked_at": revoked_at.wire,
                        }
                    )
                )
                state.pending_consent_revocation = token
                state.pending_consent_projects = None
            state.consent = LocalObservationConsent(
                workspace_commitment=consent.workspace_commitment,
                granted_at=consent.granted_at,
                revoked_at=consent.revoked_at or revoked_at,
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
                oldest = min(
                    notices,
                    key=lambda session_id: (
                        notices[session_id].recency_ordinal,
                        session_id.encode(),
                    ),
                )
                del notices[oldest]
        delivered = state.frontier_motion_delivered
        if delivered:
            for session_id in tuple(delivered):
                if _session_ended(session_id):
                    del delivered[session_id]
            while len(delivered) > _MAX_FRONTIER_MOTION_NOTICES:
                oldest = min(
                    delivered,
                    key=lambda session_id: (
                        delivered[session_id].recency_ordinal,
                        session_id.encode(),
                    ),
                )
                del delivered[oldest]

    @staticmethod
    def _session_commitment_for_key(key: str) -> str | None:
        """Return a session commitment encoded as a direct or cursor-map key."""

        candidate = key
        if not candidate.startswith("hmac-sha256:"):
            _prefix, separator, suffix = candidate.partition(":")
            if not separator:
                return None
            candidate = suffix
        try:
            return validate_commitment(candidate)
        except ProtocolValueError:
            return None

    @staticmethod
    def _session_replay_protected(state: _WorkspaceState) -> set[str]:
        """Find sessions whose replay state is still needed by live work."""

        ended = state.ended_sessions or set()
        protected = {
            session for session in (state.session_workspaces or {}) if session not in ended
        }
        protected.update((state.codex_session_bindings or {}).values())
        protected.update(state.storage_corrupt_sessions or ())
        protected.update(row.envelope.session_commitment for row in (state.pending_outbox or ()))
        protected.update(
            envelope.session_commitment
            for _codex_session_id, envelope, _reason, _quarantined_at in (state.quarantine or ())
        )
        protected.update(intent.session_commitment for intent in (state.pending_lifecycles or ()))
        return protected

    @classmethod
    def _trim_session_mapping(
        cls,
        mapping: MutableMapping[str, _SessionMapValue] | None,
        protected: set[str],
    ) -> bool:
        """Trim one session keyed map, preserving keys needed by live work."""

        if mapping is None or len(mapping) <= _MAX_SESSION_REPLAY_KEYS:
            return False
        candidates = sorted(
            (key for key in mapping if cls._session_commitment_for_key(key) not in protected),
            key=str.encode,
        )
        changed = False
        while len(mapping) > _MAX_SESSION_REPLAY_KEYS and candidates:
            mapping.pop(candidates.pop(0), None)
            changed = True
        return changed

    @classmethod
    def _prune_session_replay_maps(cls, state: _WorkspaceState) -> bool:
        """Bound ended-session replay indexes while preserving live lanes.

        ``codex_session_bindings`` is pruned by the recovery scan because it
        needs host mapping recency.  The associated generation, cursor, stream,
        and gap maps used to outlive that operation indefinitely, however.  A
        compact replay fence is retained for the oldest bounded window; entries
        still referenced by a live binding, pending row, quarantine, lifecycle
        intent, or corruption repair are never evicted.
        """

        protected = cls._session_replay_protected(state)
        changed = False

        # Keep the three lifecycle tombstone containers in lockstep.  The
        # generation counter remains the replay fence for a pruned session;
        # once the bounded history is exhausted a later reattach is treated as
        # a new local generation, which is the same bounded-retention contract
        # as the host mapping cap.
        generation_keys = set(state.session_generations or ())
        generation_keys.update(state.ended_session_generations or ())
        generation_keys.update(state.ended_sessions or ())
        tombstone_candidates = sorted(
            generation_keys - protected,
            key=str.encode,
        )
        while len(generation_keys) > _MAX_SESSION_REPLAY_KEYS and tombstone_candidates:
            session = tombstone_candidates.pop(0)
            generation_keys.discard(session)
            if state.session_generations is not None:
                changed = state.session_generations.pop(session, None) is not None or changed
            if state.ended_session_generations is not None:
                changed = state.ended_session_generations.pop(session, None) is not None or changed
            if state.ended_sessions is not None and session in state.ended_sessions:
                state.ended_sessions.discard(session)
                changed = True

        direct_maps = (
            cast(MutableMapping[str, object] | None, state.session_workspaces),
            cast(MutableMapping[str, object] | None, state.cursors),
            cast(MutableMapping[str, object] | None, state.stream_cursors),
            cast(MutableMapping[str, object] | None, state.stream_partials),
            cast(MutableMapping[str, object] | None, state.stream_call_tools),
            cast(MutableMapping[str, object] | None, state.stream_call_tool_generations),
            cast(MutableMapping[str, object] | None, state.stream_source_identities),
            cast(MutableMapping[str, object] | None, state.stream_profiles),
            cast(MutableMapping[str, object] | None, state.hook_sequences),
            cast(MutableMapping[str, object] | None, state.session_advice_suppression),
        )
        for mapping in direct_maps:
            changed = cls._trim_session_mapping(mapping, protected) or changed

        if state.stream_partial_dropped_sessions is not None:
            candidates = sorted(
                session
                for session in state.stream_partial_dropped_sessions
                if session not in protected
            )
            while (
                len(state.stream_partial_dropped_sessions) > _MAX_SESSION_REPLAY_KEYS and candidates
            ):
                state.stream_partial_dropped_sessions.discard(candidates.pop(0))
                changed = True

        if state.session_gaps is not None and len(state.session_gaps) > _MAX_SESSION_REPLAY_KEYS:
            candidates = sorted(
                (session for session in state.session_gaps if session not in protected),
                key=str.encode,
            )
            while len(state.session_gaps) > _MAX_SESSION_REPLAY_KEYS and candidates:
                del state.session_gaps[candidates.pop(0)]
                changed = True

        # ``dedup_lanes`` is already bounded by ``dedup_order`` during normal
        # ingest.  Trim malformed/legacy excess here as well, keeping the set,
        # order list, and lane attribution synchronized.
        if (
            state.dedup is not None
            and state.dedup_order is not None
            and state.dedup_lanes is not None
        ):
            for key in tuple(state.dedup_lanes):
                if key not in state.dedup:
                    del state.dedup_lanes[key]
                    changed = True
            while len(state.dedup_order) > _MAX_DEDUP:
                key = cls._select_dedup_eviction_key(state)
                if key is None:
                    break
                state.dedup_order.remove(key)
                state.dedup.discard(key)
                state.dedup_lanes.pop(key, None)
                changed = True
        return changed

    def _encode_state(
        self, workspace_commitment: str, state: _WorkspaceState, *, compact: bool = False
    ) -> bytes:
        """Encode one state to its on-disk bytes, attributing the cost (#290)."""

        encode_started = self._now_mono()
        try:
            return (
                canonical_encode(self._state_to_json(workspace_commitment, state, compact=compact))
                + b"\n"
            )
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
        replay_maps_changed = self._prune_session_replay_maps(state)
        quarantined_before = len(state.quarantine or ())
        notices_before = len(state.frontier_motion_notices or ())
        delivered_before = len(state.frontier_motion_delivered or ())
        self._prune_expired_quarantine(state)
        self._prune_frontier_motion_notices(state)
        partials_dropped = self._drop_oversized_stream_partials(state)
        if (
            projected is not None
            and not replay_maps_changed
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
                evicted_index = self._fair_envelope_index(state.envelopes)
                if evicted_index is None:
                    break
                evicted = state.envelopes.pop(evicted_index)
                assert state.gaps is not None
                self._note_gap_state(state, ObservationGapCode.TRUNCATED_PAYLOAD.value)
                self._note_session_gap_state(
                    state, evicted.session_commitment, ObservationGapCode.TRUNCATED_PAYLOAD.value
                )
                truncated = True
                payload = self._encode_state(workspace_commitment, state)
        assert state.pending_outbox is not None
        assert state.quarantine is not None
        assert state.gaps is not None
        while state.pending_outbox and len(payload) > _MAX_STATE_BYTES:
            evicted_index = self._fair_outbox_index(state.pending_outbox)
            if evicted_index is None:
                break
            row = state.pending_outbox.pop(evicted_index)
            self._quarantine_row_state(
                state,
                row,
                ObservationGapCode.OUTBOX_OVERFLOW.value,
            )
            self._note_gap_state(state, _LOCAL_OUTBOX_OVERFLOW_GAP)
            self._note_gap_state(state, ObservationGapCode.OUTBOX_QUARANTINED.value)
            self._note_session_gap_state(
                state, row.envelope.session_commitment, ObservationGapCode.OUTBOX_OVERFLOW.value
            )
            payload = self._encode_state(workspace_commitment, state)
        while state.quarantine and len(payload) > _MAX_STATE_BYTES:
            evicted = state.quarantine.pop(0)
            self._record_quarantine_eviction(state, evicted[0], evicted[1], evicted[2])
            payload = self._encode_state(workspace_commitment, state)
        if len(payload) > _MAX_STATE_BYTES:
            payload = self._encode_state(workspace_commitment, state, compact=True)
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
            assert state.session_gaps is not None
            bounded_count_loss = any(
                state.gaps.get(code) is not None and state.gaps[code].active
                for code in (_LOCAL_DEDUP_EVICTED_GAP, _LOCAL_ENVELOPE_RETENTION_GAP)
            ) or any(
                _LOCAL_DEDUP_EVICTED_GAP in gaps or _LOCAL_ENVELOPE_RETENTION_GAP in gaps
                for gaps in state.session_gaps.values()
            )
            for session, gaps in tuple(state.session_gaps.items()):
                if not (_LOCAL_DEDUP_EVICTED_GAP in gaps or _LOCAL_ENVELOPE_RETENTION_GAP in gaps):
                    self._resolve_session_gap_state(
                        state,
                        ObservationGapCode.TRUNCATED_PAYLOAD.value,
                        session,
                    )
            if not bounded_count_loss:
                self._resolve_gap_state(state, ObservationGapCode.TRUNCATED_PAYLOAD.value)
                self._resolve_gap_state(state, _LOCAL_DEDUP_EVICTED_GAP)
                self._resolve_session_gap_state(state, _LOCAL_DEDUP_EVICTED_GAP)
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
        assert state.session_gaps is not None
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
        self._note_session_gap_state(
            state,
            envelope.session_commitment,
            ObservationGapCode.QUARANTINE_DETAIL_EVICTED.value,
        )

    def _workspace_for_envelope(self, envelope: ObservationEnvelope) -> str:
        owners = self._session_workspace_owners_unlocked(envelope.session_commitment)
        if len(owners) == 1:
            return next(iter(owners))
        if owners:
            raise _error(
                PublicErrorCode.SESSION_CONFLICT,
                "Observation session has multiple workspace owners.",
                retryable=False,
            )
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
            ObservationSource.CLAUDE_HOOK: False,
            ObservationSource.CODEX_HOOK: False,
            ObservationSource.CODEX_SESSION_STREAM: False,
            ObservationSource.CURSOR_HOOK: False,
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
        current_gaps = self._current_gaps(
            state, workspace_commitment=workspace_commitment, mapping_available=mapping_available
        )
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

    def _current_gaps(
        self,
        state: _WorkspaceState,
        *,
        workspace_commitment: str,
        mapping_available: bool,
    ) -> tuple[str, ...]:
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
            _LOCAL_DEDUP_EVICTED_GAP,
            _LOCAL_ENVELOPE_RETENTION_GAP,
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
            assert state.storage_corrupt_sessions is not None
            current.update(
                reason
                for _session_id, _envelope, reason, _quarantined_at in state.quarantine
                if reason in _OBSERVATION_GAP_CODES
                and not (
                    reason == ObservationGapCode.OBSERVATION_STORAGE_CORRUPT.value
                    and not state.storage_corrupt_sessions
                )
            )
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
        # A legacy synchronous hook has acknowledged durable append, not
        # service ingest.  Until the READY forwarder consumes that fenced spool
        # file, status must report incomplete coverage rather than pretending
        # the hook reached the normal outbox.
        with contextlib.suppress(Exception):
            from yoetz.adapters.integrations.hook_spool import HookSpool

            if HookSpool(_state=self._state_root).has_pending(workspace_commitment):
                current.add(ObservationGapCode.SOURCE_LAG.value)
        # Live mapping presence outranks both the latched code and any stale row reason: a row
        # rejected for a missing mapping keeps that reason after the mapping is restored, and
        # reporting it again is the exact defect #219 filed.
        if mapping_available:
            current.discard(ObservationGapCode.MAPPING_MISSING.value)
        elif state.gaps.get(ObservationGapCode.MAPPING_MISSING.value) is not None:
            current.add(ObservationGapCode.MAPPING_MISSING.value)
        return tuple(sorted(current, key=str.encode))

    def _state_to_json(
        self, workspace: str, state: _WorkspaceState, *, compact: bool = False
    ) -> dict[str, JsonValue]:
        consent = state.consent
        assert state.session_workspaces is not None
        assert state.cursors is not None
        assert state.dedup is not None
        assert state.dedup_order is not None
        assert state.dedup_lanes is not None
        assert state.envelopes is not None
        assert state.gaps is not None
        assert state.session_gaps is not None
        assert state.unsupported_events is not None
        assert state.open_pre is not None
        assert state.stream_cursors is not None
        assert state.stream_call_tools is not None
        assert state.stream_call_tool_generations is not None
        assert state.stream_source_identities is not None
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
        dedup_order = self._ordered_dedup_keys(state)
        payload: dict[str, JsonValue] = {
            # /10 adds generation-fenced deferred host-session lifecycle intents.
            # /9 generation-fences call-id pairing and stream file identity. /8
            # persisted unfenced call-id to tool-name pairing for rollout outputs.
            # /7 attributes dropped stream-partial gaps per session. /6 adds one-shot
            # task-frontier motion notices. /5 adds terminal
            # corruption-session tracking. /3 added quarantined_at per
            # quarantine entry and the reclaimed counter. Readers tolerate both directions:
            # unknown keys are ignored and missing keys default safely.
            "schema": "yoetz.observation-local/10",
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
            # The existing field is now insertion ordered so a restart keeps
            # the same deterministic eviction sequence. Set membership still
            # remains the authoritative duplicate check.
            "dedup": tuple(dedup_order),
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
            "hook_sequence_clock": state.hook_sequence_clock,
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
                        "consecutive_reason_attempts": row.consecutive_reason_attempts,
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
        # Keep the normal local state compact.  These fields are present only during the bounded
        # revoke transaction, when the durable token and generation snapshot must survive a
        # restart; missing fields decode as no pending fence for older state files.
        if state.pending_consent_revocation is not None:
            payload["pending_consent_revocation"] = state.pending_consent_revocation
            if state.pending_consent_projects is not None:
                payload["pending_consent_projects"] = JsonObject(
                    {
                        project_id: generation
                        for project_id, generation in sorted(
                            state.pending_consent_projects.items(),
                            key=lambda item: item[0].encode(),
                        )
                    }
                )
        if state.dedup_lanes:
            payload["dedup_sessions"] = tuple(state.dedup_lanes.get(key) for key in dedup_order)
        if state.session_gaps:
            payload["session_gaps"] = JsonObject(
                {
                    session: tuple(sorted(gaps, key=str.encode))
                    for session, gaps in sorted(
                        state.session_gaps.items(), key=lambda item: item[0].encode()
                    )
                    if gaps
                }
            )
        if state.storage_corrupt_sessions:
            payload["storage_corrupt_sessions"] = tuple(
                sorted(state.storage_corrupt_sessions, key=str.encode)
            )
        if state.frontier_motion_notices or state.frontier_motion_delivered:
            payload["frontier_motion_recency"] = state.frontier_motion_recency
        if state.frontier_motion_notices:
            payload["frontier_motion_notices"] = JsonObject(
                {
                    key: JsonObject(
                        {
                            "from_sequence": notice.from_sequence,
                            "head_digest": notice.head_digest,
                            "observation_record_count": notice.observation_record_count,
                            "recency_ordinal": notice.recency_ordinal,
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
                    key: JsonObject(
                        {
                            "head_digest": value.head_digest,
                            "recency_ordinal": value.recency_ordinal,
                            "task_id": value.task_id,
                            "to_sequence": value.to_sequence,
                        }
                    )
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
        if state.stream_call_tools:
            payload["stream_call_tools"] = JsonObject(
                {
                    session: JsonObject(
                        {
                            "source_generation": state.stream_call_tool_generations[session],
                            "tools": JsonObject(
                                {
                                    call_id: tool_name
                                    for call_id, tool_name in sorted(
                                        tools.items(), key=lambda item: item[0].encode()
                                    )
                                }
                            ),
                        }
                    )
                    for session, tools in sorted(
                        state.stream_call_tools.items(), key=lambda item: item[0].encode()
                    )
                }
            )
        if state.stream_source_identities:
            payload["stream_source_identities"] = JsonObject(
                {
                    session: identity
                    for session, identity in sorted(
                        state.stream_source_identities.items(), key=lambda item: item[0].encode()
                    )
                }
            )
        if state.stream_profiles:
            payload["stream_profiles"] = JsonObject(
                {
                    session: profile_id
                    for session, profile_id in sorted(
                        state.stream_profiles.items(), key=lambda item: item[0].encode()
                    )
                }
            )
        if state.pending_lifecycles:
            payload["pending_lifecycles"] = tuple(
                JsonObject(
                    {
                        "codex_session_id": intent.codex_session_id,
                        "session_commitment": intent.session_commitment,
                        "event_kind": intent.event_kind,
                        "target_generation": intent.target_generation,
                        "clear_mapping": intent.clear_mapping,
                    }
                )
                for intent in state.pending_lifecycles
            )
        if compact:
            # The eviction ladder above preserves every authority-bearing row and records detail
            # loss in the bounded counters/gaps.  When the resulting aggregate still sits just
            # over a caller-configured byte cap, omit empty optional projections whose readers
            # already default safely.  This keeps the hard bound effective without dropping
            # consent, route generations, commitments, or loss evidence.
            for key in (
                "cursors",
                "dedup",
                "ended_sessions",
                "ended_session_generations",
                "envelopes",
                "unsupported_events",
                "last_receipt",
                "advice_frontier",
                "advice_snapshot",
                "last_advice_suppression",
                "session_advice",
                "session_advice_suppression",
                "open_pre",
                "stream_cursors",
                "stream_partials",
                "hook_sequences",
                "hook_sequence_clock",
                "last_stream_reconcile_mono_ms",
                "last_hook_receipt_mono_ms",
                "last_successful_drain_mono_ms",
                "monotonic_epoch_ms",
                "pending_outbox",
                "quarantine",
                "quarantine_reclaimed_count",
                "trusted_policy_digest",
                "trusted_policy_mac",
            ):
                value = payload.get(key)
                if value is None or value == () or value == {} or value == 0:
                    payload.pop(key, None)
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
        pending_consent_revocation = raw.get("pending_consent_revocation")
        if type(pending_consent_revocation) is not str:
            pending_consent_revocation = None
        else:
            try:
                validate_sha256_digest(pending_consent_revocation)
            except ProtocolValueError, TypeError, ValueError:
                pending_consent_revocation = None
        pending_consent_projects: dict[str, int] | None = None
        raw_pending_projects = raw.get("pending_consent_projects")
        if isinstance(raw_pending_projects, Mapping):
            pending_consent_projects = {}
            for project_id, generation in raw_pending_projects.items():
                if len(pending_consent_projects) >= _MAX_PENDING_CONSENT_PROJECTS:
                    break
                if type(project_id) is not str or type(generation) is not int:
                    continue
                if isinstance(generation, bool) or generation < 1:
                    continue
                try:
                    validate_id(IdKind.PROJECT, project_id)
                except ProtocolValueError, TypeError, ValueError:
                    continue
                pending_consent_projects[project_id] = generation
        if pending_consent_revocation is None:
            pending_consent_projects = None
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
        dedup_values = (
            cast(tuple[JsonValue, ...] | list[JsonValue], dedup_raw)
            if isinstance(dedup_raw, (tuple, list))
            else ()
        )
        dedup_order: list[str] = []
        for value in dedup_values:
            if type(value) is str and value not in dedup_order:
                dedup_order.append(value)
        dedup_lanes: dict[str, str] = {}
        dedup_sessions_raw = raw.get("dedup_sessions") or ()
        if isinstance(dedup_sessions_raw, (tuple, list)):
            for key, lane in zip(dedup_order, dedup_sessions_raw, strict=False):
                if type(lane) is not str:
                    continue
                try:
                    validate_commitment(lane)
                except ProtocolValueError:
                    continue
                dedup_lanes[key] = lane
        ended_sessions_raw = raw.get("ended_sessions") or ()
        session_generations = {
            str(key): int(value)
            for key, value in cast(
                Mapping[str, JsonValue], raw.get("session_generations") or {}
            ).items()
            if type(value) is int
            and not isinstance(value, bool)
            and 0 <= value <= _MAX_SAFE_INTEGER
        }
        ended_session_generations = {
            str(key): int(value)
            for key, value in cast(
                Mapping[str, JsonValue], raw.get("ended_session_generations") or {}
            ).items()
            if type(value) is int and not isinstance(value, bool) and value >= 1
        }
        pending_lifecycles: list[PendingSessionLifecycle] = []
        for item in cast(
            tuple[JsonValue, ...] | list[JsonValue], raw.get("pending_lifecycles") or ()
        ):
            if not isinstance(item, Mapping):
                continue
            pending = cast(Mapping[str, JsonValue], item)
            try:
                pending_lifecycles.append(
                    PendingSessionLifecycle(
                        codex_session_id=pending.get("codex_session_id"),  # type: ignore[arg-type]
                        session_commitment=pending.get("session_commitment"),  # type: ignore[arg-type]
                        event_kind=pending.get("event_kind"),  # type: ignore[arg-type]
                        target_generation=pending.get("target_generation"),  # type: ignore[arg-type]
                        clear_mapping=pending.get("clear_mapping", False),  # type: ignore[arg-type]
                    )
                )
            except ProtocolValueError, TypeError, ValueError:
                continue
            if len(pending_lifecycles) >= _MAX_PENDING_LIFECYCLES:
                break
        envelopes_raw = raw.get("envelopes") or ()
        gaps_raw = raw.get("gaps") or ()
        gap_history: dict[str, _GapState] = {}
        session_gaps: dict[str, set[str]] = {}
        raw_session_gaps = raw.get("session_gaps") or {}
        if isinstance(raw_session_gaps, Mapping):
            for session, codes in cast(Mapping[str, JsonValue], raw_session_gaps).items():
                if len(session_gaps) >= _MAX_ENVELOPES:
                    break
                if type(session) is not str or not isinstance(codes, (tuple, list)):
                    continue
                try:
                    validate_commitment(session)
                except ProtocolValueError:
                    continue
                retained_codes = {
                    code
                    for code in cast(tuple[JsonValue, ...] | list[JsonValue], codes)
                    if type(code) is str
                    and (
                        code in _OBSERVATION_GAP_CODES
                        or code
                        in {
                            _LOCAL_DEDUP_EVICTED_GAP,
                            _LOCAL_ENVELOPE_RETENTION_GAP,
                            _LOCAL_OUTBOX_OVERFLOW_GAP,
                            _LOCAL_STREAM_PARTIAL_DROPPED_GAP,
                        }
                    )
                }
                if retained_codes:
                    session_gaps[session] = set(
                        sorted(retained_codes, key=str.encode)[:_MAX_SESSION_GAP_CODES]
                    )
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
        stream_call_tools: dict[str, dict[str, str]] = {}
        stream_call_tool_generations: dict[str, int] = {}
        remaining_call_tools = _MAX_STREAM_CALL_TOOLS
        stream_call_tools_raw = raw.get("stream_call_tools")
        if isinstance(stream_call_tools_raw, Mapping):
            for session, values in cast(Mapping[str, JsonValue], stream_call_tools_raw).items():
                if remaining_call_tools == 0:
                    break
                if type(session) is not str or not isinstance(values, Mapping):
                    continue
                values_row = cast(Mapping[str, JsonValue], values)
                generation = values_row.get("source_generation")
                raw_tools = values_row.get("tools")
                if (
                    type(generation) is not int
                    or generation < 1
                    or not isinstance(raw_tools, Mapping)
                ):
                    # /8 entries were not generation-fenced and are discarded.
                    continue
                tools: dict[str, str] = {}
                for call_id, tool_name in cast(Mapping[str, JsonValue], raw_tools).items():
                    if (
                        remaining_call_tools == 0
                        or type(call_id) is not str
                        or type(tool_name) is not str
                        or not call_id
                        or not tool_name
                        or len(call_id) > 128
                        or len(tool_name) > 128
                    ):
                        continue
                    tools[call_id] = tool_name
                    remaining_call_tools -= 1
                if tools:
                    stream_call_tools[session] = tools
                    stream_call_tool_generations[session] = generation
        stream_source_identities = {
            str(session): identity
            for session, identity in cast(
                Mapping[str, JsonValue], raw.get("stream_source_identities") or {}
            ).items()
            if type(session) is str
            and type(identity) is str
            and identity.startswith("hmac-sha256:")
            and len(identity) == 76
        }
        stream_profiles = {
            str(session): profile_id
            for session, profile_id in cast(
                Mapping[str, JsonValue], raw.get("stream_profiles") or {}
            ).items()
            if type(session) is str
            and type(profile_id) is str
            and _stream_profile_id_valid(profile_id)
        }
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
        raw_hook_sequence_clock = raw.get("hook_sequence_clock", 0)
        hook_sequence_clock = (
            raw_hook_sequence_clock
            if type(raw_hook_sequence_clock) is int
            and not isinstance(raw_hook_sequence_clock, bool)
            and 0 <= raw_hook_sequence_clock <= _MAX_SAFE_INTEGER
            else max(hook_sequences.values(), default=0)
        )
        hook_sequence_clock = max(hook_sequence_clock, max(hook_sequences.values(), default=0))
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
            consecutive_raw = row.get("consecutive_reason_attempts")
            if (
                type(consecutive_raw) is int
                and not isinstance(consecutive_raw, bool)
                and 0 <= consecutive_raw <= attempts
            ):
                consecutive_reason_attempts = consecutive_raw
            else:
                # A legacy omission or malformed counter cannot prove that all
                # historical attempts used last_reason. Start the reason-local
                # streak at zero; the next attempt persists the bounded counter,
                # so compatibility grants at most one fresh retry budget.
                consecutive_reason_attempts = 0
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
                        consecutive_reason_attempts=consecutive_reason_attempts,
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
        raw_frontier_motion_recency = raw.get("frontier_motion_recency", 0)
        frontier_motion_recency = (
            raw_frontier_motion_recency
            if type(raw_frontier_motion_recency) is int
            and 0 <= raw_frontier_motion_recency <= _MAX_SAFE_INTEGER
            else 0
        )
        frontier_motion_recency = max(
            [
                frontier_motion_recency,
                *(notice.recency_ordinal for notice in frontier_motion_notices.values()),
                *(mark.recency_ordinal for mark in frontier_motion_delivered.values()),
            ]
        )
        state = _WorkspaceState(
            consent=consent,
            session_workspaces=session_workspaces,
            cursors=cursors,
            dedup=set(dedup_order),
            dedup_order=dedup_order,
            dedup_lanes=dedup_lanes,
            ended_sessions=set(cast(tuple[str, ...], ended_sessions_raw)),
            session_generations=session_generations,
            ended_session_generations=ended_session_generations,
            pending_lifecycles=pending_lifecycles,
            pending_consent_revocation=pending_consent_revocation,
            pending_consent_projects=pending_consent_projects,
            envelopes=envelopes,
            gaps=gap_history,
            session_gaps=session_gaps,
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
            frontier_motion_recency=frontier_motion_recency,
            open_pre=open_pre,
            stream_cursors=stream_cursors,
            stream_partials=stream_partials,
            stream_call_tools=stream_call_tools,
            stream_call_tool_generations=stream_call_tool_generations,
            stream_source_identities=stream_source_identities,
            stream_profiles=stream_profiles,
            stream_partial_dropped_sessions=stream_partial_dropped_sessions,
            hook_sequences=hook_sequences,
            hook_sequence_clock=hook_sequence_clock,
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
        if any(notice.recency_ordinal == 0 for notice in frontier_motion_notices.values()) or any(
            mark.recency_ordinal == 0 for mark in frontier_motion_delivered.values()
        ):
            # Notices persisted before the LRU clock existed load with ordinal
            # 0. Fold them into the clock once so they hold a real position
            # instead of tying at zero until the next touch.
            _renumber_frontier_motion_recency(state)
        return state


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
