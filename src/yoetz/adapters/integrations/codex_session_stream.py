"""Incremental Codex session-stream observer (selective secondary source)."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from yoetz.adapters.importers.codex_jsonl import (
    CodexCapabilityProfile,
    CodexParsedRecord,
)
from yoetz.adapters.importers.codex_rollout_jsonl import (
    ROLLOUT_MAX_LINE_BYTES,
    SUPPORTED_ROLLOUT_PROFILES,
    parse_codex_rollout_jsonl_from_offset,
    profile_for_rollout_id,
    profile_for_rollout_version,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    YOETZ_TOOL_NAMES,
    LocalObservationStore,
    self_observation_deliverable,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
    stream_line_commitment,
)
from yoetz.domain.values import JsonObject, JsonValue, Timestamp, timestamp_from_datetime
from yoetz.ports.importer import ImportLineStatus
from yoetz.protocol.canonical import canonical_digest, canonical_encode

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
    "stream_profile_from_id",
    "structural_from_stream_record",
]

_MAX_READ_CHUNK: Final = 262_144
_EMPTY_COMMITMENT: Final = "hmac-sha256:" + ("0" * 64)
_MAX_SESSION_WALK: Final = 4_096
_MAX_CANONICAL_INTEGER: Final = (1 << 53) - 1
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


_JSONL_SUFFIXES: Final = (".jsonl", ".jsonl.zst")
# Union vocabularies are only a fallback for callers that map a record without naming the
# profile that admitted it; the reader always passes the exact admitted profile.
_ROLLOUT_ITEM_TYPES: Final = frozenset(
    item for profile in SUPPORTED_ROLLOUT_PROFILES.values() for item in profile.item_types
)
_ROLLOUT_WRAPPER_TYPES: Final = frozenset(
    wrapper for profile in SUPPORTED_ROLLOUT_PROFILES.values() for wrapper in profile.wrapper_types
)
_OVERSIZED_PARTIAL_PREFIX: Final = b"\x00yoetz-oversized-line/v1\x00"
_OVERSIZED_PARTIAL_DOMAIN: Final = b"yoetz/observation-stream-oversized-state/v1\x00"
_OVERSIZED_LINE_DOMAIN: Final = b"yoetz/observation-stream-oversized-line/v1\x00"


@dataclass(frozen=True, slots=True)
class _OversizedLineState:
    line_start: int
    prefix_digest: str


def _encode_oversized_partial(
    *,
    line_start: int,
    prefix_commitment: str,
    session_commitment: str,
    source_generation: int,
    source_identity: str,
    key_material: bytes,
) -> bytes:
    digest = prefix_commitment.removeprefix("hmac-sha256:")
    if (
        type(line_start) is not int
        or line_start < 0
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("session_stream_partial_invalid")
    body = f"{line_start}:{digest}".encode("ascii")
    context = (
        session_commitment.encode("ascii")
        + b"\x00"
        + str(source_generation).encode("ascii")
        + b"\x00"
        + source_identity.encode("ascii")
        + b"\x00"
        + body
    )
    tag = hmac.new(key_material, _OVERSIZED_PARTIAL_DOMAIN + context, hashlib.sha256).hexdigest()
    return _OVERSIZED_PARTIAL_PREFIX + tag.encode("ascii") + b":" + body


def _decode_oversized_partial(
    value: bytes,
    *,
    session_commitment: str,
    source_generation: int,
    source_identity: str,
    key_material: bytes,
) -> _OversizedLineState | None:
    if not value.startswith(_OVERSIZED_PARTIAL_PREFIX):
        return None
    try:
        tag, line_start_raw, prefix_digest_raw = value[len(_OVERSIZED_PARTIAL_PREFIX) :].split(b":")
        line_start = int(line_start_raw.decode("ascii"))
        prefix_digest = prefix_digest_raw.decode("ascii")
    except UnicodeError, ValueError:
        raise ValueError("session_stream_partial_invalid") from None
    body = line_start_raw + b":" + prefix_digest_raw
    context = (
        session_commitment.encode("ascii")
        + b"\x00"
        + str(source_generation).encode("ascii")
        + b"\x00"
        + source_identity.encode("ascii")
        + b"\x00"
        + body
    )
    expected_tag = (
        hmac.new(key_material, _OVERSIZED_PARTIAL_DOMAIN + context, hashlib.sha256)
        .hexdigest()
        .encode("ascii")
    )
    if (
        line_start < 0
        or line_start_raw != str(line_start).encode("ascii")
        or len(prefix_digest) != 64
        or any(char not in "0123456789abcdef" for char in prefix_digest)
        or not hmac.compare_digest(tag, expected_tag)
    ):
        raise ValueError("session_stream_partial_invalid")
    return _OversizedLineState(line_start, prefix_digest)


def _oversized_line_commitment(
    *,
    state: _OversizedLineState,
    byte_end: int,
    session_commitment: str,
    source_generation: int,
    source_identity: str,
    key_material: bytes,
) -> str:
    body = (
        session_commitment.encode("ascii")
        + b"\x00"
        + str(source_generation).encode("ascii")
        + b"\x00"
        + source_identity.encode("ascii")
        + b"\x00"
        + f"{state.line_start}:{byte_end}:{state.prefix_digest}".encode("ascii")
    )
    digest = hmac.new(key_material, _OVERSIZED_LINE_DOMAIN + body, hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def default_stream_profile() -> CodexCapabilityProfile:
    """Return the baseline rollout profile.

    Session-stream reconciliation does not parse under this profile by default: the exact
    ``cli_version`` in each stream's session header selects the admitted profile, and the reader
    remembers that selection per source generation. The baseline exists for callers and tests
    that need one concrete supported profile.
    """

    try:
        return profile_for_rollout_version("0.148.0")
    except ValueError:
        return next(iter(SUPPORTED_ROLLOUT_PROFILES.values()))


def stream_profile_from_id(profile_id: str | None) -> CodexCapabilityProfile | None:
    """Resolve a persisted profile id to its exact profile; unknown ids resolve to ``None``."""

    if profile_id is None:
        return None
    try:
        return profile_for_rollout_id(profile_id)
    except ValueError:
        return None


def _now() -> Timestamp:
    current = datetime.now(UTC)
    stamp = current.replace(microsecond=(current.microsecond // 1000) * 1000)
    return timestamp_from_datetime(stamp)


def _source_file_identity(facts: os.stat_result, key_material: bytes) -> str:
    """Return a private, path-free identity for one opened stream generation."""

    def bounded(value: int) -> int | str:
        if -_MAX_CANONICAL_INTEGER <= value <= _MAX_CANONICAL_INTEGER:
            return value
        return f"hex:{value:x}"

    payload = canonical_encode(
        JsonObject(
            {
                "device": bounded(facts.st_dev),
                "inode": bounded(facts.st_ino),
            }
        )
    )
    return "hmac-sha256:" + hmac.new(key_material, payload, hashlib.sha256).hexdigest()


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
            candidate = self._validate_candidate(
                Path(hook_provided_path), home=home, session_id=token
            )
            if candidate is None:
                return None
            if candidate.name.lower().endswith(".jsonl.zst"):
                uncompressed = self._validate_candidate(
                    candidate.with_suffix(""), home=home, session_id=token
                )
                if uncompressed is not None:
                    return uncompressed
            return candidate
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
        uncompressed_matches: list[Path] = []
        compressed_matches: list[Path] = []
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
                        target = (
                            compressed_matches
                            if validated.name.lower().endswith(".jsonl.zst")
                            else uncompressed_matches
                        )
                        target.append(validated)
        except OSError:
            return None
        if len(uncompressed_matches) == 1:
            return uncompressed_matches[0]
        if uncompressed_matches or len(compressed_matches) != 1:
            return None
        return compressed_matches[0]

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
        if session_id not in resolved.name:
            return None
        if not self._owner_safe(resolved):
            return None
        lower_name = resolved.name.lower()
        if not any(lower_name.endswith(suffix) for suffix in _JSONL_SUFFIXES):
            return None
        compressed = lower_name.endswith(".jsonl.zst")
        if compressed:
            return resolved
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


def _normalize_tool_name(value: object) -> str | None:
    token = _token(value)
    if token is None:
        return None
    if token.startswith("mcp__yoetz__") and token in YOETZ_TOOL_NAMES:
        return token
    if token in YOETZ_TOOL_NAMES:
        return token
    return token


def _structural_body(record: CodexParsedRecord) -> JsonObject | None:
    payload = record.value.get("payload")
    if isinstance(payload, JsonObject):
        inner = payload.get("item")
        if isinstance(inner, JsonObject):
            return inner
        return payload
    item = record.value.get("item")
    if isinstance(item, JsonObject):
        return item
    return None


def structural_from_stream_record(
    record: CodexParsedRecord,
    *,
    profile: CodexCapabilityProfile | None = None,
) -> tuple[JsonObject, tuple[str, ...]]:
    """Map a parsed stream record to allowlisted structural fields + opaque gaps.

    ``profile`` is the exact profile that admitted the record; without it the union of every
    supported vocabulary decides which ``type`` tokens are semantic rather than tool names.
    """

    item_types = _ROLLOUT_ITEM_TYPES if profile is None else frozenset(profile.item_types)
    known_wrappers = _ROLLOUT_WRAPPER_TYPES if profile is None else frozenset(profile.wrapper_types)
    gaps: set[str] = set()
    fields: dict[str, JsonValue] = {"stream_kind": record.wrapper_type}
    item_type = record.item_type
    if item_type is not None:
        token = _token(item_type)
        if token is not None:
            fields["action"] = token
        else:
            gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
    body = _structural_body(record)
    if body is not None:
        tool = _normalize_tool_name(body.get("tool")) or _normalize_tool_name(body.get("name"))
        type_token = _token(body.get("type"))
        if tool is None and type_token is not None and type_token not in item_types:
            tool = _normalize_tool_name(type_token)
        if tool is not None:
            fields["tool_name"] = tool
        status = _token(body.get("status")) or _token(body.get("result_status"))
        if status is not None:
            fields["result_status"] = status
        exit_code = body.get("exit_code")
        if "exit_code" in body:
            if type(exit_code) is int and -1 <= exit_code <= 255:
                fields["exit_status"] = exit_code
            else:
                gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
        call_id = _token(body.get("id")) or _token(body.get("call_id"))
        if call_id is not None:
            fields["tool_call_id"] = call_id
    if record.wrapper_type not in known_wrappers:
        gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
    return JsonObject(fields), tuple(sorted(gaps, key=str.encode))


def envelope_from_stream_record(
    record: CodexParsedRecord,
    *,
    session_commitment: str,
    cursor: ObservationCursor,
    profile: CodexCapabilityProfile | None = None,
) -> ObservationEnvelope:
    structural, gaps = structural_from_stream_record(record, profile=profile)
    host_ids: dict[str, JsonValue] = {}
    body = _structural_body(record)
    if body is not None:
        for key in ("id", "call_id", "tool_call_id", "event_id"):
            token = _token(body.get(key))
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
                    "source_generation": cursor.source_generation,
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


def _opaque_stream_envelope(
    *,
    session_commitment: str,
    cursor: ObservationCursor,
) -> ObservationEnvelope:
    """Retain one unsupported complete line without interpreting its payload."""

    identity = (
        "stream:"
        + canonical_digest(
            JsonObject(
                {
                    "byte_end": cursor.byte_position,
                    "line_commitment": cursor.last_source_commitment,
                    "ordinal": cursor.event_position,
                    "source_generation": cursor.source_generation,
                    "wrapper": "unsupported_event",
                }
            )
        ).removeprefix("sha256:")[:48]
    )
    return ObservationEnvelope(
        session_commitment=session_commitment,
        event_kind="unsupported_event",
        source_identity=identity,
        source=ObservationSource.CODEX_SESSION_STREAM,
        cursor=cursor,
        receipt_time=_now(),
        structural_payload=JsonObject({}),
        content_object_refs=(),
        gap_codes=(ObservationGapCode.UNSUPPORTED_EVENT.value,),
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
    # The exact profile the current source generation's header admitted, or ``None`` until the
    # header is read. Never a default: every generation re-selects from its own header.
    profile: CodexCapabilityProfile | None
    cursor: ObservationCursor
    key_material: bytes
    partial_line: bytes = b""
    _inode: int | None = None
    _source_identity: str | None = None
    _size_at_generation: int = 0

    def __post_init__(self) -> None:
        if type(self.partial_line) is not bytes:
            raise ValueError("session_stream_partial_invalid")
        if type(self.key_material) is not bytes or not 16 <= len(self.key_material) <= 64:
            raise ValueError("session_stream_key_invalid")

    @property
    def source_identity(self) -> str | None:
        """Return the private identity of the source inspected by the latest advance."""

        return self._source_identity

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
        source_identity = _source_file_identity(stat, self.key_material)
        size = stat.st_size
        generation = self.cursor.source_generation
        byte_position = self.cursor.byte_position
        event_position = self.cursor.event_position
        last_commitment = self.cursor.last_source_commitment

        if (self._source_identity is not None and source_identity != self._source_identity) or (
            self._inode is not None and inode is not None and inode != self._inode
        ):
            # Rotation: new inode → new generation.
            rotated = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            self.profile = None
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif size < byte_position:
            # Truncation / rewrite in place.
            truncated = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            self.profile = None
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif event_position > 0 and self.profile is None:
            # An admitted generation whose exact profile is not recorded cannot be parsed
            # under any vocabulary without inferring one. Replay it from the header under a
            # fresh generation instead; earlier bytes are re-read, never skipped.
            restarted = True
            generation += 1
            byte_position = 0
            event_position = 0
            self.partial_line = b""
            last_commitment = _EMPTY_COMMITMENT
            gaps.add(ObservationGapCode.CURSOR_STALE.value)
        elif byte_position == 0 and event_position == 0 and self.partial_line == b"":
            restarted = generation > 1 or (self._inode is not None)

        oversized_state: _OversizedLineState | None = None
        if self.partial_line.startswith(_OVERSIZED_PARTIAL_PREFIX):
            try:
                oversized_state = _decode_oversized_partial(
                    self.partial_line,
                    session_commitment=self.session_commitment,
                    source_generation=generation,
                    source_identity=source_identity,
                    key_material=self.key_material,
                )
                if (
                    oversized_state is None
                    or oversized_state.line_start > byte_position
                    or byte_position - oversized_state.line_start <= ROLLOUT_MAX_LINE_BYTES
                ):
                    raise ValueError("session_stream_partial_invalid")
            except ValueError:
                # The private continuation marker is authenticated and bound to this session,
                # generation, and source. An invalid/transplanted marker cannot authorize a skip;
                # restart the generation and replay from admission instead.
                restarted = True
                generation += 1
                byte_position = 0
                event_position = 0
                last_commitment = _EMPTY_COMMITMENT
                self.partial_line = b""
                self.profile = None
                oversized_state = None
                gaps.add(ObservationGapCode.CURSOR_STALE.value)
                gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)

        self._inode = inode if type(inode) is int else self._inode
        self._source_identity = source_identity
        self._size_at_generation = size

        if event_position == 0 and byte_position > 0 and not self.partial_line:
            # Refused admission is durable for this generation: consumed bytes with
            # no admitted event mean the first complete line was rejected, so no
            # later append may materialize without an accepted exact header. Only
            # rotation or truncation (a fresh generation) re-opens admission.
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position,
                event_position=0,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
            return SessionStreamAdvance(
                (), cursor, b"", tuple(sorted(gaps, key=str.encode)), restarted, truncated, rotated
            )

        partial_source_bytes = 0 if oversized_state is not None else len(self.partial_line)
        to_read = min(_MAX_READ_CHUNK, max(0, size - (byte_position + partial_source_bytes)))
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

        # A retained partial first line has not established admission. Keep requiring the exact
        # session header until a complete admitted line advances the durable cursor.
        require_admission = event_position == 0
        try:
            with path.open("rb") as handle:
                handle.seek(byte_position + partial_source_bytes)
                chunk = handle.read(to_read)
                data = chunk if oversized_state is not None else self.partial_line + chunk
                if oversized_state is None and b"\n" not in data:
                    # A live JSONL writer can leave a legal line unterminated across hook passes.
                    # Read ahead only when the ordinary chunk contains no delimiter, and never
                    # beyond the profile's admitted line bound plus its terminator. This lets a
                    # previously dropped read-cache tail recover as soon as the newline arrives
                    # without making every routine reconcile scan a full maximum-sized line.
                    unread = max(0, size - (byte_position + len(data)))
                    read_ahead = min(
                        unread,
                        max(0, ROLLOUT_MAX_LINE_BYTES + 1 - len(data)),
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

        if oversized_state is not None:
            newline = data.find(b"\n")
            if newline < 0:
                cursor = ObservationCursor(
                    source_generation=generation,
                    byte_position=byte_position + len(data),
                    event_position=event_position,
                    last_source_commitment=last_commitment,
                    mapping_version=STREAM_MAPPING_VERSION,
                )
                self.cursor = cursor
                self.partial_line = _encode_oversized_partial(
                    line_start=oversized_state.line_start,
                    prefix_commitment=f"hmac-sha256:{oversized_state.prefix_digest}",
                    session_commitment=self.session_commitment,
                    source_generation=generation,
                    source_identity=source_identity,
                    key_material=self.key_material,
                )
                gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
                if not data and size > byte_position:
                    gaps.add(ObservationGapCode.SOURCE_LAG.value)
                return SessionStreamAdvance(
                    (),
                    cursor,
                    self.partial_line,
                    tuple(sorted(gaps, key=str.encode)),
                    restarted,
                    truncated,
                    rotated,
                )

            byte_end = byte_position + newline + 1
            last_commitment = _oversized_line_commitment(
                state=oversized_state,
                byte_end=byte_end,
                session_commitment=self.session_commitment,
                source_generation=generation,
                source_identity=source_identity,
                key_material=self.key_material,
            )
            if not require_admission:
                event_position += 1
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_end,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            self.partial_line = b""
            if require_admission:
                # The oversized first line never established admission. Leaving
                # the ordinal at zero with consumed bytes keeps this generation
                # durably refused instead of quietly admitting later appends.
                gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
                oversized_envelopes: tuple[ObservationEnvelope, ...] = ()
            else:
                gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
                oversized_envelopes = (
                    _opaque_stream_envelope(
                        session_commitment=self.session_commitment,
                        cursor=cursor,
                    ),
                )
            return SessionStreamAdvance(
                oversized_envelopes,
                cursor,
                b"",
                tuple(sorted(gaps, key=str.encode)),
                restarted,
                truncated,
                rotated,
            )

        if b"\n" not in data and len(data) > ROLLOUT_MAX_LINE_BYTES:
            prefix_commitment = stream_line_commitment(self.key_material, data)
            self.partial_line = _encode_oversized_partial(
                line_start=byte_position,
                prefix_commitment=prefix_commitment,
                session_commitment=self.session_commitment,
                source_generation=generation,
                source_identity=source_identity,
                key_material=self.key_material,
            )
            cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position + len(data),
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
            self.cursor = cursor
            gaps.add(ObservationGapCode.TRUNCATED_PAYLOAD.value)
            return SessionStreamAdvance(
                (),
                cursor,
                self.partial_line,
                tuple(sorted(gaps, key=str.encode)),
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
            # Admission always re-selects from the header: the profile is the one the exact
            # ``cli_version`` names, never a caller default or the previous generation's choice.
            parsed = parse_codex_rollout_jsonl_from_offset(
                data,
                None if require_admission else self.profile,
                start_ordinal=1,
                require_admission=require_admission,
            )
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

        if parsed.profile is not None:
            self.profile = parsed.profile
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
            last_commitment = stream_line_commitment(self.key_material, line.content)
            record = next(
                (item for item in parsed.records if item.line_ordinal == line.ordinal),
                None,
            )
            if record is None:
                reason = parsed.reason_codes[index] if index < len(parsed.reason_codes) else None
                status = parsed.statuses[index] if index < len(parsed.statuses) else None
                if reason == "unsupported_codex_profile" or (
                    require_admission
                    and index == 0
                    and status in {ImportLineStatus.MALFORMED, ImportLineStatus.OVERSIZED}
                ):
                    # A refused or never-admitted line consumes its bytes but
                    # never advances the event ordinal, so admission stays
                    # required instead of silently lapsing after a rejected
                    # header once event_position moved past zero.
                    gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
                    continue
                event_position += 1
                if index < len(parsed.statuses):
                    gaps.add(ObservationGapCode.UNSUPPORTED_EVENT.value)
                    envelopes.append(
                        _opaque_stream_envelope(
                            session_commitment=self.session_commitment,
                            cursor=ObservationCursor(
                                source_generation=generation,
                                byte_position=byte_position + consumed,
                                event_position=event_position,
                                last_source_commitment=last_commitment,
                                mapping_version=STREAM_MAPPING_VERSION,
                            ),
                        )
                    )
                continue
            event_position += 1
            abs_cursor = ObservationCursor(
                source_generation=generation,
                byte_position=byte_position + consumed,
                event_position=event_position,
                last_source_commitment=last_commitment,
                mapping_version=STREAM_MAPPING_VERSION,
            )
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
                    profile=self.profile,
                )
            )

        if "unsupported_codex_profile" in parsed.stream_gaps and consumed > 0:
            # A refused chunk holds no tail: the durable refused state is exactly
            # (event_position == 0, consumed bytes, empty partial), and every
            # later line of a refused generation is equally unsupported.
            hold = b""
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
            elif gap == "unsupported_codex_profile":
                gaps.add(ObservationGapCode.UNSUPPORTED_FORMAT.value)
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


def _stream_phase(structural: Mapping[str, JsonValue]) -> str:
    """Map a rollout tool record to the hook phase the delivery policy speaks."""

    action = structural.get("action")
    if action in {"function_call", "custom_tool_call"}:
        return "PreToolUse"
    if action in {"function_call_output", "custom_tool_call_output"}:
        return "PostToolUse"
    return ""


def _pair_stream_tool_name(
    envelope: ObservationEnvelope, call_tools: dict[str, str]
) -> ObservationEnvelope:
    structural = cast(Mapping[str, JsonValue], envelope.structural_payload)
    action = structural.get("action")
    call_id = _token(structural.get("tool_call_id"))
    tool_name = _token(structural.get("tool_name"))
    if call_id is None:
        return envelope
    if action in {"function_call", "custom_tool_call"} and tool_name is not None:
        if call_id not in call_tools and len(call_tools) >= 256:
            call_tools.pop(next(iter(call_tools)))
        call_tools[call_id] = tool_name
        return envelope
    if action not in {"function_call_output", "custom_tool_call_output"}:
        return envelope
    paired = call_tools.get(call_id)
    if paired is None:
        return replace(
            envelope,
            gap_codes=tuple(
                sorted(
                    {*envelope.gap_codes, ObservationGapCode.UNPAIRED_EVENT.value},
                    key=str.encode,
                )
            ),
        )
    gaps = set(envelope.gap_codes)
    if tool_name is not None and tool_name != paired:
        gaps.add(ObservationGapCode.DEDUP_CONFLICT.value)
    return replace(
        envelope,
        structural_payload=JsonObject({**dict(structural), "tool_name": paired}),
        gap_codes=tuple(sorted(gaps, key=str.encode)),
    )


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
    return reconcile_session_stream_path(
        store,
        workspace_commitment=workspace_commitment,
        session_commitment=session_commitment,
        codex_session_id=codex_session_id,
        path=path,
    )


def reconcile_session_stream_path(
    store: LocalObservationStore,
    *,
    workspace_commitment: str,
    session_commitment: str,
    codex_session_id: str,
    path: Path,
) -> dict[str, JsonValue]:
    """Reconcile a locally selected path with the same durable frontier on every entry point."""

    if path.name.lower().endswith(".jsonl.zst"):
        store.note_coverage_gap(workspace_commitment, ObservationGapCode.UNSUPPORTED_FORMAT.value)
        store.note_stream_reconcile(workspace_commitment)
        return {
            "accepted": 0,
            "duplicates": 0,
            "gaps": (ObservationGapCode.UNSUPPORTED_FORMAT.value,),
            "byte_position": 0,
            "event_position": 0,
            "generation": 1,
            "rotated": False,
            "truncated": False,
            "resolved": True,
        }
    existing = store.get_stream_cursor(workspace_commitment, session_commitment)
    mapping_reset = existing is not None and existing.mapping_version != STREAM_MAPPING_VERSION
    if existing is None or mapping_reset:
        existing = ObservationCursor(
            source_generation=(1 if existing is None else existing.source_generation + 1),
            byte_position=0,
            event_position=0,
            last_source_commitment=_EMPTY_COMMITMENT,
            mapping_version=STREAM_MAPPING_VERSION,
        )
    partial = (
        b"" if mapping_reset else store.get_stream_partial(workspace_commitment, session_commitment)
    )
    call_tools = (
        {}
        if mapping_reset
        else store.stream_call_tools_for_session(
            workspace_commitment,
            session_commitment,
            source_generation=existing.source_generation,
        )
    )
    prior_call_tools = dict(call_tools)
    source_identity = (
        None
        if mapping_reset
        else store.stream_source_identity_for_session(workspace_commitment, session_commitment)
    )
    # The profile persisted with the cursor is the one this generation's header admitted. A
    # missing or no-longer-supported id makes the reader replay from the header (CURSOR_STALE)
    # rather than parse admitted lines under a guessed vocabulary.
    prior_profile_id = (
        None
        if mapping_reset
        else store.stream_profile_for_session(workspace_commitment, session_commitment)
    )
    reader = SessionStreamReader(
        session_commitment=session_commitment,
        profile=stream_profile_from_id(prior_profile_id),
        cursor=existing,
        key_material=store.key_material(),
        partial_line=partial,
        _source_identity=source_identity,
    )
    advance = reader.advance(path)
    if advance.cursor.source_generation != existing.source_generation:
        call_tools.clear()
    accepted = 0
    duplicates = 0
    overflow = False
    delivery_blocked = False
    committed_cursor = existing
    for unpaired_envelope in advance.envelopes:
        candidate_call_tools = dict(call_tools)
        envelope = _pair_stream_tool_name(unpaired_envelope, candidate_call_tools)
        result = store.ingest(envelope)
        if result.disposition.value not in {"accepted", "duplicate"}:
            delivery_blocked = True
            break
        # Enqueue duplicates too: the local observation row may have committed
        # immediately before an earlier enqueue overflow/crash. This closes the
        # retry hole without growing the outbox because enqueue is idempotent.
        # A Yoetz-owned call the stream recorded is the same self-observation
        # the hook already classified: retained locally, delivered only when it
        # is distinct evidence (#564). The cursor still advances past it.
        if (
            self_observation_deliverable(
                _stream_phase(envelope.structural_payload), envelope.structural_payload
            )
            and store.enqueue_outbox(workspace_commitment, codex_session_id, envelope)
            == ObservationGapCode.OUTBOX_OVERFLOW.value
        ):
            overflow = True
            break
        call_tools = candidate_call_tools
        committed_cursor = envelope.cursor
        if result.disposition.value == "accepted":
            accepted += 1
        else:
            duplicates += 1
    if not overflow and not delivery_blocked:
        committed_cursor = advance.cursor
    source_read_complete = ObservationGapCode.SOURCE_LAG.value not in advance.gaps
    progress_committed = committed_cursor != existing
    if progress_committed or (not overflow and not delivery_blocked and source_read_complete):
        persisted_partial = (
            advance.partial_line
            if not overflow and not delivery_blocked and committed_cursor == advance.cursor
            else (
                partial
                if partial.startswith(_OVERSIZED_PARTIAL_PREFIX) and committed_cursor == existing
                else b""
            )
        )
        persisted_call_tools = call_tools
        same_generation = committed_cursor.source_generation == advance.cursor.source_generation
        persisted_identity = reader.source_identity if same_generation else source_identity
        persisted_profile_id = (
            (None if reader.profile is None else reader.profile.profile_id)
            if same_generation
            else prior_profile_id
        )
    else:
        # No cursor progress means no new source identity or pairing state may
        # commit: the next process must rediscover rotation and replay line 1.
        persisted_partial = partial
        persisted_call_tools = prior_call_tools
        persisted_identity = source_identity
        persisted_profile_id = prior_profile_id
    store.set_stream_reconcile_state(
        workspace_commitment,
        session_commitment,
        cursor=committed_cursor,
        partial=persisted_partial,
        call_tools=persisted_call_tools,
        source_identity=persisted_identity,
        profile_id=persisted_profile_id,
    )
    store.note_stream_reconcile(workspace_commitment)
    gaps = advance.gaps
    for durable_gap in (
        ObservationGapCode.UNSUPPORTED_EVENT.value,
        ObservationGapCode.UNSUPPORTED_FORMAT.value,
    ):
        if durable_gap in gaps:
            store.note_coverage_gap(workspace_commitment, durable_gap)
    if overflow and ObservationGapCode.OUTBOX_OVERFLOW.value not in gaps:
        gaps = (*gaps, ObservationGapCode.OUTBOX_OVERFLOW.value)
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "gaps": gaps,
        "byte_position": committed_cursor.byte_position,
        "event_position": committed_cursor.event_position,
        "generation": committed_cursor.source_generation,
        "profile_id": persisted_profile_id,
        "rotated": advance.rotated,
        "truncated": advance.truncated,
        "resolved": True,
    }
