"""Crash-safe, structural-only ingress spool for synchronous legacy hooks.

The hook process deliberately does not open the observation state document.  It
only appends one bounded structural record; the READY service owns hydration,
mapping, outbox insertion and forwarding.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.domain.observation import workspace_commitment_from_path
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse

__all__ = ["HookSpool", "SpooledHookObservation"]

_MAX_RECORD_BYTES: Final = 8 * 1024
_SAFE_FIELDS: Final = frozenset(
    {
        "session_id",
        "tool_name",
        "tool_use_id",
        "tool_call_id",
        "correlation_id",
        "parent_tool_call_id",
        "permission_decision",
        "permission_kind",
        "decision_reason_code",
        "result_status",
        "subagent_id",
        "claim_kind",
        "action",
        "changed_paths_digest",
        "mapping_hint",
        "capability_profile_id",
        "codex_version",
        "exit_status",
        "duration_ms",
        "attempt",
        "success",
        "denied",
        "decision",
    }
)


@dataclass(frozen=True, slots=True)
class SpooledHookObservation:
    workspace_commitment: str
    event_name: str
    payload: Mapping[str, JsonValue]


class HookSpool:
    """An owner-only append spool with rename-based, replay-safe consumption."""

    def __init__(self, *, _state: Path | None = None) -> None:
        root = state_dir() if _state is None else _state
        self._root = root / "hook-spool"
        self._key_path = root / "observation" / "key-material.bin"

    def workspace_commitment(self, workspace: str) -> str:
        return workspace_commitment_from_path(self._key_material(), workspace)

    def append(self, *, workspace: str, event_name: str, payload: Mapping[str, JsonValue]) -> bool:
        """Durably append one bounded structural observation.

        This is intentionally the only write on a legacy host's critical path.
        It never opens the observation store or contacts the service.
        """

        if type(event_name) is not str or not event_name or len(event_name) > 128:
            return False
        safe = {key: value for key, value in payload.items() if key in _SAFE_FIELDS}
        session_id = safe.get("session_id")
        if type(session_id) is not str or not session_id or len(session_id) > 128:
            return False
        safe["_yoetz_spool_id"] = str(uuid.uuid4())
        commitment = self.workspace_commitment(workspace)
        body = {
            "event": event_name,
            "payload": safe,
            "workspace_commitment": commitment,
        }
        try:
            line = canonical_encode(cast(JsonValue, body)) + b"\n"
        except TypeError, ValueError:
            return False
        if len(line) > _MAX_RECORD_BYTES:
            return False
        try:
            ensure_owner_only_dir(self._root)
            path = self._root / f"{commitment.removeprefix('hmac-sha256:')}.jsonl"
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_APPEND
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                written = 0
                while written < len(line):
                    written += os.write(descriptor, line[written:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return True
        except OSError, PathSafetyError:
            return False

    def pending_workspaces(self) -> tuple[str, ...]:
        try:
            ensure_owner_only_dir(self._root)
            names = list(self._root.glob("*.jsonl")) + list(self._root.glob("*.draining"))
        except OSError, PathSafetyError:
            return ()
        result: set[str] = set()
        for path in names:
            if path.is_symlink() or not path.is_file() or len(path.stem) != 64:
                continue
            result.add(f"hmac-sha256:{path.stem}")
        return tuple(sorted(result, key=str.encode))

    def has_pending(self, workspace_commitment: str) -> bool:
        digest = workspace_commitment.removeprefix("hmac-sha256:")
        return len(digest) == 64 and any(
            (self._root / f"{digest}{suffix}").exists() for suffix in (".jsonl", ".draining")
        )

    @contextlib.contextmanager
    def claim(self, workspace_commitment: str) -> Iterator[tuple[SpooledHookObservation, ...]]:
        """Fence one file by rename; failure leaves it for at-least-once replay."""

        digest = workspace_commitment.removeprefix("hmac-sha256:")
        pending = self._root / f"{digest}.jsonl"
        draining = self._root / f"{digest}.draining"
        if len(digest) != 64:
            yield ()
            return
        try:
            ensure_owner_only_dir(self._root)
            if pending.exists() and not draining.exists():
                os.replace(pending, draining)
            if not draining.exists() or draining.is_symlink():
                yield ()
                return
            raw = draining.read_bytes()
            rows = tuple(self._parse_line(line, workspace_commitment) for line in raw.splitlines())
            records = tuple(row for row in rows if row is not None)
            yield records
        except OSError, PathSafetyError:
            yield ()
            return
        else:
            with contextlib.suppress(OSError):
                draining.unlink()

    @staticmethod
    def _parse_line(line: bytes, workspace: str) -> SpooledHookObservation | None:
        if not line or len(line) > _MAX_RECORD_BYTES:
            return None
        try:
            value = strict_json_parse(line)
        except Exception:
            return None
        if not isinstance(value, Mapping):
            return None
        event = value.get("event")
        payload = value.get("payload")
        commitment = value.get("workspace_commitment")
        if event is None or commitment != workspace or not isinstance(payload, Mapping):
            return None
        if type(event) is not str or type(commitment) is not str:
            return None
        return SpooledHookObservation(
            workspace_commitment=commitment,
            event_name=event,
            payload=cast(Mapping[str, JsonValue], payload),
        )

    def _key_material(self) -> bytes:
        ensure_owner_only_dir(self._root)
        try:
            descriptor = os.open(
                self._key_path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError as exc:
            raise OSError("hook_spool_key_missing") from exc
        else:
            try:
                key = os.read(descriptor, 65)
            finally:
                os.close(descriptor)
            if not 16 <= len(key) <= 64:
                raise OSError("hook_spool_key_invalid")
            return key
