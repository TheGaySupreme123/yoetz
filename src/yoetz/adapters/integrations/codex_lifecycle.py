"""Private Codex↔Yoetz structural lifecycle correlation mapping store."""

from __future__ import annotations

import contextlib
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.ids import IdKind, is_valid_id, validate_id

__all__ = [
    "MAPPING_VERSION",
    "LifecycleMapping",
    "acquire_session_lock",
    "clear_mapping",
    "codex_lifecycle_dir",
    "encode_frontier_token",
    "load_mapping",
    "mapping_from_start_ids",
    "mapping_path",
    "parse_frontier_token",
    "store_mapping",
    "validate_codex_session_id",
]

MAPPING_VERSION: Final = 1
_MAPPING_KEYS: Final = frozenset(
    {
        "mapping_version",
        "codex_session_id",
        "yoetz_task_id",
        "yoetz_session_id",
        "yoetz_writer_id",
        "last_frontier",
    }
)
_MAX_MAPPING_BYTES: Final = 4_096
_MAX_CODEX_SESSION_ID_CHARS: Final = 128
_MAX_FRONTIER_TOKEN_CHARS: Final = 128
_LOCK_STALE_SECONDS: Final = 30.0
_CODEX_SESSION_ID_RE: Final = re.compile(r"^[!-~]+$", re.ASCII)
_FRONTIER_DIGEST_RE: Final = re.compile(
    r"^(?:genesis|sha256:[0-9a-f]{64})$",
    re.ASCII,
)


@dataclass(frozen=True, slots=True)
class LifecycleMapping:
    """Allowlisted structural Codex session → Yoetz identity correlation."""

    mapping_version: int
    codex_session_id: str
    yoetz_task_id: str
    yoetz_session_id: str
    yoetz_writer_id: str
    last_frontier: str | None

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "mapping_version": self.mapping_version,
            "codex_session_id": self.codex_session_id,
            "yoetz_task_id": self.yoetz_task_id,
            "yoetz_session_id": self.yoetz_session_id,
            "yoetz_writer_id": self.yoetz_writer_id,
            "last_frontier": self.last_frontier,
        }


def validate_codex_session_id(value: object) -> str:
    """Validate a bounded opaque Codex session token (no path separators)."""

    if type(value) is not str:
        raise ProtocolValueError("id_wrong_type")
    if not 1 <= len(value) <= _MAX_CODEX_SESSION_ID_CHARS:
        raise ProtocolValueError("id_wrong_length")
    if "/" in value or "\\" in value or "\0" in value:
        raise ProtocolValueError("id_malformed_uuid")
    if _CODEX_SESSION_ID_RE.fullmatch(value) is None:
        raise ProtocolValueError("id_not_ascii")
    return value


def encode_frontier_token(*, sequence: str, head_digest: str) -> str:
    """Encode a frontier as a bounded opaque structural token."""

    if type(sequence) is not str or type(head_digest) is not str:
        raise ProtocolValueError("invalid_frontier")
    if not sequence.isascii() or not sequence.isdecimal():
        raise ProtocolValueError("invalid_frontier")
    if _FRONTIER_DIGEST_RE.fullmatch(head_digest) is None:
        raise ProtocolValueError("invalid_frontier")
    token = f"{sequence}:{head_digest}"
    if len(token) > _MAX_FRONTIER_TOKEN_CHARS:
        raise ProtocolValueError("invalid_frontier")
    return token


def parse_frontier_token(token: str) -> tuple[str, str]:
    """Parse a previously encoded frontier token."""

    if type(token) is not str or len(token) > _MAX_FRONTIER_TOKEN_CHARS:
        raise ProtocolValueError("invalid_frontier")
    sequence, separator, digest = token.partition(":")
    if separator != ":" or not sequence or not digest:
        raise ProtocolValueError("invalid_frontier")
    validated = encode_frontier_token(sequence=sequence, head_digest=digest)
    left, _, right = validated.partition(":")
    return left, right


def _ensure_lifecycle_dir(path: Path) -> None:
    try:
        ensure_owner_only_dir(path)
    except PathSafetyError:
        # Concurrent creators can race on mkdir; accept an already-private directory.
        if not path.is_dir() or path.is_symlink():
            raise
        mode = path.stat().st_mode & 0o777
        if mode != 0o700:
            raise


def codex_lifecycle_dir(*, _state: Path | None = None) -> Path:
    """Return the private plugin lifecycle state directory under the Yoetz state root."""

    root = state_dir() if _state is None else _state
    path = root / "codex-lifecycle"
    _ensure_lifecycle_dir(root)
    _ensure_lifecycle_dir(path)
    return path


def mapping_path(codex_session_id: str, *, _state: Path | None = None) -> Path:
    """Return the mapping file path for one validated Codex session id."""

    session_id = validate_codex_session_id(codex_session_id)
    return codex_lifecycle_dir(_state=_state) / f"{session_id}.json"


def _parse_mapping(
    raw: Mapping[str, JsonValue], *, expected_session: str | None
) -> LifecycleMapping:
    if frozenset(raw) != _MAPPING_KEYS:
        raise ProtocolValueError("unsupported_json_type")
    version = raw.get("mapping_version")
    if type(version) is not int or version != MAPPING_VERSION:
        raise ProtocolValueError("unsupported_json_type")
    codex_session_id = validate_codex_session_id(raw.get("codex_session_id"))
    if expected_session is not None and codex_session_id != expected_session:
        raise ProtocolValueError("unsupported_json_type")
    task_id = validate_id(IdKind.TASK, raw.get("yoetz_task_id"))
    session_id = validate_id(IdKind.SESSION, raw.get("yoetz_session_id"))
    writer_id = validate_id(IdKind.WRITER, raw.get("yoetz_writer_id"))
    frontier = raw.get("last_frontier")
    if frontier is not None:
        if type(frontier) is not str:
            raise ProtocolValueError("invalid_frontier")
        parse_frontier_token(frontier)
    return LifecycleMapping(
        mapping_version=MAPPING_VERSION,
        codex_session_id=codex_session_id,
        yoetz_task_id=task_id,
        yoetz_session_id=session_id,
        yoetz_writer_id=writer_id,
        last_frontier=frontier,
    )


def load_mapping(codex_session_id: str, *, _state: Path | None = None) -> LifecycleMapping | None:
    """Load one mapping; malformed/unknown-version/oversized files are treated as absent."""

    try:
        path = mapping_path(codex_session_id, _state=_state)
    except ProtocolValueError:
        return None
    try:
        if not path.is_file() or path.is_symlink():
            return None
        size = path.stat().st_size
        if size <= 0 or size > _MAX_MAPPING_BYTES:
            return None
        raw_bytes = path.read_bytes()
        if len(raw_bytes) > _MAX_MAPPING_BYTES:
            return None
        parsed = strict_json_parse(raw_bytes)
        if not isinstance(parsed, Mapping):
            return None
        return _parse_mapping(
            cast(Mapping[str, JsonValue], parsed), expected_session=codex_session_id
        )
    except OSError, ProtocolValueError, UnicodeError, TypeError, ValueError:
        return None


def store_mapping(mapping: LifecycleMapping, *, _state: Path | None = None) -> None:
    """Atomically write one allowlisted mapping (0600). Rejects non-allowlisted content."""

    if type(mapping) is not LifecycleMapping:
        raise ProtocolValueError("unsupported_json_type")
    validated = _parse_mapping(mapping.to_wire(), expected_session=mapping.codex_session_id)
    path = mapping_path(validated.codex_session_id, _state=_state)
    encoded = canonical_encode(validated.to_wire()) + b"\n"
    if len(encoded) > _MAX_MAPPING_BYTES:
        raise ProtocolValueError("unsupported_json_type")
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        view = memoryview(encoded)
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
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def clear_mapping(codex_session_id: str, *, _state: Path | None = None) -> None:
    """Remove one mapping file if present; ignores absence."""

    try:
        path = mapping_path(codex_session_id, _state=_state)
    except ProtocolValueError:
        return
    with contextlib.suppress(OSError, FileNotFoundError):
        if path.is_file() and not path.is_symlink():
            path.unlink()


def _lock_path(codex_session_id: str, *, _state: Path | None = None) -> Path:
    session_id = validate_codex_session_id(codex_session_id)
    return codex_lifecycle_dir(_state=_state) / f".{session_id}.lock"


def acquire_session_lock(
    codex_session_id: str,
    *,
    _state: Path | None = None,
    stale_seconds: float = _LOCK_STALE_SECONDS,
) -> _SessionLock:
    """Acquire a per-session lock file; context manager yields True when this caller owns the lock.

    Duplicate concurrent session-start events must not stampede the service. A stale lock older
    than ``stale_seconds`` is broken once. Callers that do not acquire the lock should no-op.
    """

    return _SessionLock(codex_session_id, _state=_state, stale_seconds=stale_seconds)


class _SessionLock:
    __slots__ = ("_owned", "_path", "_stale_seconds")

    def __init__(
        self,
        codex_session_id: str,
        *,
        _state: Path | None,
        stale_seconds: float,
    ) -> None:
        if type(stale_seconds) is not float or not 0.1 <= stale_seconds <= 300.0:
            raise ValueError("lock_stale_timeout_invalid")
        self._path = _lock_path(codex_session_id, _state=_state)
        self._stale_seconds = stale_seconds
        self._owned = False

    def __enter__(self) -> bool:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags, 0o600)
            try:
                payload = f"{os.getpid()}:{time.time_ns()}\n".encode("ascii")
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._owned = True
        except FileExistsError:
            try:
                age = time.time() - self._path.stat().st_mtime
            except OSError:
                age = 0.0
            if age >= self._stale_seconds:
                with contextlib.suppress(OSError):
                    self._path.unlink()
                try:
                    descriptor = os.open(self._path, flags, 0o600)
                    os.close(descriptor)
                    self._owned = True
                except FileExistsError:
                    self._owned = False
        return self._owned

    def __exit__(self, *_exc: object) -> None:
        if self._owned:
            with contextlib.suppress(OSError):
                self._path.unlink()


def mapping_from_start_ids(
    *,
    codex_session_id: str,
    yoetz_task_id: str,
    yoetz_session_id: str,
    yoetz_writer_id: str,
    last_frontier: str | None,
) -> LifecycleMapping:
    """Build a validated mapping from already-extracted structural IDs."""

    if last_frontier is not None and not (
        type(last_frontier) is str and len(last_frontier) <= _MAX_FRONTIER_TOKEN_CHARS
    ):
        raise ProtocolValueError("invalid_frontier")
    if last_frontier is not None:
        parse_frontier_token(last_frontier)
    if not is_valid_id(IdKind.TASK, yoetz_task_id):
        raise ProtocolValueError("id_wrong_prefix")
    return LifecycleMapping(
        mapping_version=MAPPING_VERSION,
        codex_session_id=validate_codex_session_id(codex_session_id),
        yoetz_task_id=validate_id(IdKind.TASK, yoetz_task_id),
        yoetz_session_id=validate_id(IdKind.SESSION, yoetz_session_id),
        yoetz_writer_id=validate_id(IdKind.WRITER, yoetz_writer_id),
        last_frontier=last_frontier,
    )
