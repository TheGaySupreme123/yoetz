"""Incremental Codex session-stream observer (selective secondary source)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from yoetz.adapters.importers.codex_jsonl import (
    SUPPORTED_CODEX_PROFILES,
    CodexCapabilityProfile,
    CodexParsedRecord,
    parse_codex_jsonl_from_offset,
    profile_for_codex_version,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    LocalObservationStore,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
    stream_line_commitment,
)
from yoetz.domain.values import JsonObject, JsonValue, Timestamp, timestamp_from_datetime
from yoetz.protocol.canonical import canonical_digest

__all__ = [
    "CodexSessionStreamLocator",
    "PERIODIC_RECONCILE_SECONDS",
    "SessionStreamAdvance",
    "SessionStreamReader",
    "default_stream_profile",
    "envelope_from_stream_record",
    "reconcile_session_stream",
    "resolve_codex_home",
    "should_trigger_stream_reconcile",
    "structural_from_stream_record",
]

_MAX_READ_CHUNK: Final = 262_144
_EMPTY_COMMITMENT: Final = "hmac-sha256:" + ("0" * 64)
_MAX_SESSION_WALK: Final = 4_096
PERIODIC_RECONCILE_SECONDS: Final = 30.0
_MATERIAL_HOOK_EVENTS: Final = frozenset(
    {
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "PreCompact",
        "PostCompact",
        "Stop",
        "SessionEnd",
        "SessionStart",
        "SubagentStop",
    }
)


def default_stream_profile() -> CodexCapabilityProfile:
    # Prefer the pinned supported profile; fall back to the sole registered one.
    try:
        return profile_for_codex_version("0.139.0")
    except ValueError:
        return next(iter(SUPPORTED_CODEX_PROFILES.values()))


def _now() -> Timestamp:
    current = datetime.now(UTC)
    stamp = current.replace(microsecond=(current.microsecond // 1000) * 1000)
    return timestamp_from_datetime(stamp)


def _token(value: object) -> str | None:
    if type(value) is not str or not value or len(value) > 128:
        return None
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:/+-"
    if any(ch not in allowed for ch in value) or value[0] in "._:/+-":
        return None
    return value


def resolve_codex_home(
    explicit: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the selected owner-private Codex home (never disclosed by callers)."""

    if explicit is not None:
        return Path(explicit).expanduser()
    environ = os.environ if env is None else env
    for key in ("CODEX_HOME", "CODEX_TESTING_HOME"):
        raw = environ.get(key)
        if type(raw) is str and raw.strip():
            return Path(raw).expanduser()
    return Path.home() / ".codex"


@dataclass(frozen=True, slots=True, repr=False)
class CodexSessionStreamLocator:
    """Locate a Codex session JSONL path under one selected Codex home.

    Resolved paths are for local use only: never persist or emit them.
    """

    codex_home: Path

    def __repr__(self) -> str:
        return "CodexSessionStreamLocator(codex_home=<redacted>)"

    @property
    def session_root(self) -> Path:
        return self.codex_home / "sessions"

    def resolve(
        self,
        *,
        session_id: str,
        hook_provided_path: str | None = None,
    ) -> Path | None:
        """Return a validated session file or ``None`` when unsafe/ambiguous/absent."""

        token = _token(session_id)
        if token is None:
            return None
        home = self._validated_home()
        if home is None:
            return None
        if hook_provided_path is not None:
            return self._validate_candidate(Path(hook_provided_path), home=home, session_id=token)
        return self._exact_session_match(home=home, session_id=token)

    def _validated_home(self) -> Path | None:
        try:
            home = self.codex_home.expanduser()
            if home.is_symlink() or not home.is_dir():
                return None
            resolved = home.resolve(strict=True)
        except OSError:
            return None
        if not self._owner_safe(resolved):
            return None
        return resolved

    def _exact_session_match(self, *, home: Path, session_id: str) -> Path | None:
        root = home / "sessions"
        try:
            if root.is_symlink() or not root.is_dir():
                return None
            root_resolved = root.resolve(strict=True)
        except OSError:
            return None
        if not self._is_beneath(root_resolved, home):
            return None
        matches: list[Path] = []
        walked = 0
        try:
            for dirpath, dirnames, filenames in os.walk(root_resolved, followlinks=False):
                walked += 1
                if walked > _MAX_SESSION_WALK:
                    return None
                # Never descend through symlinked directories.
                dirnames[:] = [name for name in dirnames if not (Path(dirpath) / name).is_symlink()]
                for name in filenames:
                    if session_id not in name:
                        continue
                    candidate = Path(dirpath) / name
                    validated = self._validate_candidate(
                        candidate, home=home, session_id=session_id
                    )
                    if validated is not None:
                        matches.append(validated)
                    if len(matches) > 1:
                        return None
        except OSError:
            return None
        if len(matches) != 1:
            return None
        return matches[0]

    def _validate_candidate(self, candidate: Path, *, home: Path, session_id: str) -> Path | None:
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return None
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        if not self._is_beneath(resolved, home / "sessions"):
            return None
        if not self._is_beneath(resolved, home):
            return None
        if resolved.suffix.lower() != ".jsonl":
            return None
        if session_id not in resolved.name:
            return None
        if not self._owner_safe(resolved):
            return None
        # Unsupported / non-JSONL formats: require an opening JSON object byte.
        try:
            with resolved.open("rb") as handle:
                head = handle.read(1)
        except OSError:
            return None
        if head not in {b"{", b"", b"\n"}:
            return None
        return resolved

    @staticmethod
    def _is_beneath(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root.resolve(strict=False))
        except OSError, ValueError:
            return False
        return True

    @staticmethod
    def _owner_safe(path: Path) -> bool:
        try:
            stat = path.stat()
        except OSError:
            return False
        uid = getattr(os, "getuid", None)
        if callable(uid):
            return int(stat.st_uid) == cast(Callable[[], int], uid)()
        return True


def structural_from_stream_record(record: CodexParsedRecord) -> tuple[JsonObject, tuple[str, ...]]:
    """Map a parsed stream record to allowlisted structural fields + opaque gaps."""

    gaps: set[str] = set()
    fields: dict[str, JsonValue] = {"stream_kind": record.wrapper_type}
    item_type = record.item_type
    if item_type is not None:
        token = _token(item_type)
        if token is not None:
            fields["action"] = token
        else:
            gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
    item = record.value.get("item")
    if isinstance(item, JsonObject):
        tool = _token(item.get("tool")) or _token(item.get("name")) or _token(item.get("type"))
        if tool is not None:
            fields["tool_name"] = tool
        status = _token(item.get("status")) or _token(item.get("result_status"))
        if status is not None:
            fields["result_status"] = status
        exit_code = item.get("exit_code")
        if type(exit_code) is int and not isinstance(exit_code, bool) and 0 <= exit_code <= 2**31:
            fields["exit_status"] = exit_code
        call_id = _token(item.get("id")) or _token(item.get("call_id"))
        if call_id is not None:
            fields["tool_call_id"] = call_id
    # Unknown future wrapper/item shapes: keep envelope, never invent success.
    known_wrappers = {
        "error",
        "item.completed",
        "item.started",
        "item.updated",
        "thread.started",
        "turn.completed",
        "turn.failed",
        "turn.started",
    }
    if record.wrapper_type not in known_wrappers:
        gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
    return JsonObject(fields), tuple(sorted(gaps, key=str.encode))


def envelope_from_stream_record(
    record: CodexParsedRecord,
    *,
    session_commitment: str,
    cursor: ObservationCursor,
) -> ObservationEnvelope:
    structural, gaps = structural_from_stream_record(record)
    host_ids: dict[str, JsonValue] = {}
    item = record.value.get("item")
    if isinstance(item, JsonObject):
        for key in ("id", "call_id", "tool_call_id", "event_id"):
            token = _token(item.get(key))
            if token is not None:
                host_ids[key] = token
    event_id = _token(record.value.get("event_id")) or _token(record.value.get("id"))
    if event_id is not None:
        host_ids.setdefault("event_id", event_id)
    identity = (
        "stream:"
        + canonical_digest(
            JsonObject(
                {
                    "byte_end": record.byte_end,
                    "host_ids": JsonObject(host_ids),
                    "ordinal": record.line_ordinal,
                    "structural": structural,
                    "wrapper": record.wrapper_type,
                }
            )
        ).removeprefix("sha256:")[:48]
    )
    event_kind = _token(record.wrapper_type) or "unsupported_event"
    return ObservationEnvelope(
        session_commitment=session_commitment,
        event_kind=event_kind,
        source_identity=identity,
        source=ObservationSource.CODEX_SESSION_STREAM,
        cursor=cursor,
        receipt_time=_now(),
        structural_payload=structural,
        content_object_refs=(),
        gap_codes=gaps,
    )


@dataclass(frozen=True, slots=True)
class SessionStreamAdvance:
    envelopes: tuple[ObservationEnvelope, ...]
    cursor: ObservationCursor
    partial_line: bytes
    gaps: tuple[str, ...]
    restarted: bool
    truncated: bool
    rotated: bool


@dataclass
class SessionStreamReader:
    """Incremental JSONL reader with generation-fenced cursor and partial-line hold."""

    session_commitment: str
    profile: CodexCapabilityProfile
    cursor: ObservationCursor
    key_material: bytes
    partial_line: bytes = b""
    _inode: int | None = None
    _size_at_generation: int = 0

    def __post_init__(self) -> None:
        if type(self.partial_line) is not bytes:
            raise ValueError("session_stream_partial_invalid")
        if type(self.key_material) is not bytes or not 16 <= len(self.key_material) <= 64:
            raise ValueError("session_stream_key_invalid")

    def advance(self, path: Path) -> SessionStreamAdvance:
        gaps: set[str] = set()
        restarted = False
        truncated = False
        rotated = False
        try:
            stat = path.stat()
        except OSError:
            gaps.add(ObservationGapCode.SOURCE_LAG.value)
            return SessionStreamAdvance(
                (), self.cursor, self.partial_line, tuple(sorted(gaps)), False, False, False
            )

        inode = getattr(stat, "st_ino", None)
        size = stat.st_size
        generation = self.cursor.source_generation
        byte_position = self.cursor.byte_position
        event_position = self.cursor.event_position
        last_commitment = self.cursor.last_source_commitment

        if self._inode is not None and inode is not None and inode != self._inode:
            # Rotation: new inode → new generation.
            rotated = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif size < byte_position:
            # Truncation / rewrite in place.
            truncated = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif byte_position == 0 and event_position == 0 and self.partial_line == b"":
            restarted = generation > 1 or (self._inode is not None)

        self._inode = inode if type(inode) is int else self._inode
        self._size_at_generation = size

        to_read = min(_MAX_READ_CHUNK, max(0, size - (byte_position + len(self.partial_line))))
        if to_read == 0 and not self.partial_line:
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            return SessionStreamAdvance(
                (), cursor, self.partial_line, tuple(sorted(gaps)), restarted, truncated, rotated
            )

        try:
            with path.open("rb") as handle:
                handle.seek(byte_position + len(self.partial_line))
                chunk = handle.read(to_read)
                data = self.partial_line + chunk
                if b"\n" not in data:
                    # A live JSONL writer can leave a legal line unterminated across hook passes.
                    # Read ahead only when the ordinary chunk contains no delimiter, and never
                    # beyond the profile's admitted line bound plus its terminator. This lets a
                    # previously dropped read-cache tail recover as soon as the newline arrives
                    # without making every routine reconcile scan a full maximum-sized line.
                    unread = max(0, size - (byte_position + len(data)))
                    read_ahead = min(
                        unread,
                        max(0, self.profile.max_line_bytes + 1 - len(data)),
                    )
                    if read_ahead:
                        data += handle.read(read_ahead)
        except OSError:
            gaps.add(ObservationGapCode.SOURCE_LAG.value)
            return SessionStreamAdvance(
                (),
                self.cursor,
                self.partial_line,
                tuple(sorted(gaps)),
                restarted,
                truncated,
                rotated,
            )

        if not data:
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            return SessionStreamAdvance(
                (), cursor, b"", tuple(sorted(gaps)), restarted, truncated, rotated
            )

        try:
            parsed = parse_codex_jsonl_from_offset(data, self.profile, start_ordinal=1)
        except ValueError:
            gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            return SessionStreamAdvance(
                (), cursor, self.partial_line, tuple(sorted(gaps)), restarted, truncated, rotated
            )

        consumed = 0
        envelopes: list[ObservationEnvelope] = []
        hold = b""
        for index, line in enumerate(parsed.lines):
            if not line.terminated:
                hold = line.content
                if "truncated_final_line" in parsed.stream_gaps:
                    gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
                break
            consumed = line.byte_end
            event_position += 1
            last_commitment = stream_line_commitment(self.key_material, line.content)
            abs_cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position + consumed,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            record = next(
                (item for item in parsed.records if item.line_ordinal == line.ordinal),
                None,
            )
            if record is None:
                if index < len(parsed.statuses):
                    gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
                continue
            # Rewrite record ordinal to the absolute event position for identity stability.
            positioned = CodexParsedRecord(
                event_position,
                record.byte_start,
                record.byte_end,
                record.wrapper_type,
                record.item_type,
                record.value,
            )
            envelopes.append(
                envelope_from_stream_record(
                    positioned,
                    session_commitment=self.session_commitment,
                    cursor=abs_cursor,
                )
            )

        new_byte = byte_position + consumed
        self.partial_line = hold
        cursor = ObservationCursor(
            source_generation=generation,
            byte_position=new_byte,
            event_position=event_position,
            last_source_commitment=last_commitment,
            mapping_version=STREAM_MAPPING_VERSION,
        )
        self.cursor = cursor
        for gap in parsed.stream_gaps:
            if gap in {"truncated_final_line", "final_newline_absent"}:
                gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
        return SessionStreamAdvance(
            tuple(envelopes),
            cursor,
            self.partial_line,
            tuple(sorted(gaps, key=str.encode)),
            restarted,
            truncated,
            rotated,
        )


def should_trigger_stream_reconcile(
    event_name: str,
    *,
    last_reconcile_mono: float | None,
    now_mono: float | None = None,
    session_source: str | None = None,
) -> bool:
    """Decide whether an observe hook should run incremental stream reconciliation."""

    if event_name in {"Stop", "SessionEnd", "PostCompact", "PreCompact"}:
        return True
    if event_name == "SessionStart" and session_source in {"resume", "compact"}:
        return True
    if event_name in _MATERIAL_HOOK_EVENTS:
        return True
    current = time.monotonic() if now_mono is None else now_mono
    if last_reconcile_mono is None:
        return False
    return (current - last_reconcile_mono) >= PERIODIC_RECONCILE_SECONDS


def reconcile_session_stream(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    session_commitment: str,
    codex_session_id: str,
    locator: CodexSessionStreamLocator,
    hook_provided_path: str | None = None,
) -> dict[str, JsonValue]:
    """Advance the session stream cursor and ingest envelopes. Path stays local-only."""

    path = locator.resolve(session_id=codex_session_id, hook_provided_path=hook_provided_path)
    if path is None:
        return {
            "accepted": 0,
            "duplicates": 0,
            "gaps": (ObservationGapCode.SOURCE_LAG.value,),
            "resolved": False,
        }
    existing = store.get_stream_cursor(workspace_commitment, session_commitment)
    if existing is None:
        existing = ObservationCursor(
            source_generation=1,
            byte_position=0,
            event_position=0,
            last_source_commitment=_EMPTY_COMMITMENT,
            mapping_version=STREAM_MAPPING_VERSION,
        )
    partial = store.get_stream_partial(workspace_commitment, session_commitment)
    reader = SessionStreamReader(
        session_commitment=session_commitment,
        profile=default_stream_profile(),
        cursor=existing,
        key_material=store.key_material(),
        partial_line=partial,
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
        # Enqueue duplicates too: the local observation row may have committed
        # immediately before an earlier enqueue overflow/crash. This closes the
        # retry hole without growing the outbox because enqueue is idempotent.
        if (
            store.enqueue_outbox(workspace_commitment, codex_session_id, envelope)
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
    # A partial tail belongs to ``advance.cursor``. When overflow leaves the
    # cursor behind, discard that tail and reread from the last queued line.
    store.set_stream_partial(
        workspace_commitment,
        session_commitment,
        advance.partial_line if not overflow else b"",
    )
    store.note_stream_reconcile(workspace_commitment)
    gaps = advance.gaps
    if overflow and ObservationGapCode.OUTBOX_OVERFLOW.value not in gaps:
        gaps = (*gaps, ObservationGapCode.OUTBOX_OVERFLOW.value)
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "gaps": gaps,
        "byte_position": committed_cursor.byte_position,
        "event_position": committed_cursor.event_position,
        "generation": committed_cursor.source_generation,
        "rotated": advance.rotated,
        "truncated": advance.truncated,
        "resolved": True,
    }
