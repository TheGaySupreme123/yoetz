"""Founder-authorized elevated bootstrap consent (ADR-015).

Pending consent is owner-only file state. Secrets enter only through inherited FDs on approve.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from yoetz.config.paths import ensure_owner_only_dir, state_dir
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode

__all__ = [
    "ElevatedBootstrapError",
    "ElevatedOperation",
    "PendingElevatedConsent",
    "approve_pending",
    "clear_pending",
    "load_pending",
    "prepare_pending",
    "projection_for_status",
    "read_secret_fd",
    "status_payload",
]

ElevatedOperation = Literal["vault_initialize", "provider_credential_set"]

_SCHEMA: Final = "yoetz.elevated-bootstrap.pending/1"
_TTL_SECONDS: Final = 15 * 60
_PHRASE_BYTES: Final = 3
_PENDING_NAME: Final = "elevated-bootstrap-pending.json"
_AUDIT_NAME: Final = "elevated-bootstrap-audit.jsonl"


class ElevatedBootstrapError(Exception):
    """Bounded elevated-bootstrap failure with a stable reason token."""

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or not reason:
            raise TypeError("elevated_reason_invalid")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class PendingElevatedConsent:
    """One active elevated-bootstrap pending consent record."""

    pending_id: str
    operation: ElevatedOperation
    danger_text: str
    danger_digest: str
    confirmation_phrase: str
    created_at_unix: int
    expires_at_unix: int
    target_digest: str
    provider_binding: Mapping[str, str] | None

    def as_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema": _SCHEMA,
            "pending_id": self.pending_id,
            "state": "pending",
            "operation": self.operation,
            "danger_text": self.danger_text,
            "danger_digest": self.danger_digest,
            "confirmation_phrase": self.confirmation_phrase,
            "created_at_unix": self.created_at_unix,
            "expires_at_unix": self.expires_at_unix,
            "target_digest": self.target_digest,
        }
        if self.provider_binding is not None:
            payload["provider_binding"] = dict(self.provider_binding)
        return payload


def elevated_dir(*, _state: Path | None = None) -> Path:
    root = state_dir() if _state is None else _state
    path = root / "elevated-bootstrap"
    ensure_owner_only_dir(path)
    return path


def pending_path(*, _state: Path | None = None) -> Path:
    return elevated_dir(_state=_state) / _PENDING_NAME


def audit_path(*, _state: Path | None = None) -> Path:
    return elevated_dir(_state=_state) / _AUDIT_NAME


def _danger_text(operation: ElevatedOperation) -> str:
    if operation == "vault_initialize":
        return (
            "DANGER — elevated vault initialize (ADR-015). This creates the local Yoetz vault "
            "passphrase for this installation without a controlling /dev/tty ceremony. A cloud "
            "agent will supply the passphrase on an inherited file descriptor after you confirm. "
            "Malicious same-UID code could race this window. Prefer the interactive "
            "`yoetz service initialize-passphrase` ceremony on a local terminal when possible. "
            "Confirm only if you intend this cloud-agent bootstrap."
        )
    return (
        "DANGER — elevated provider credential set (ADR-015). This stores an LLM API credential "
        "in the local Yoetz vault without a controlling /dev/tty ceremony. A cloud agent will "
        "supply reauthentication and credential bytes on inherited file descriptors after you "
        "confirm. Never paste API keys into chat. Prefer `yoetz provider credential set` on a "
        "local terminal when possible. Confirm only if you intend this cloud-agent bootstrap."
    )


def _confirmation_phrase() -> str:
    return f"YOETZ APPROVE {secrets.token_hex(_PHRASE_BYTES).upper()}"


def _danger_digest(
    *,
    operation: ElevatedOperation,
    danger_text: str,
    confirmation_phrase: str,
    target_digest: str,
    pending_id: str,
    expires_at_unix: int,
    provider_binding: Mapping[str, str] | None,
) -> str:
    body: dict[str, JsonValue] = {
        "confirmation_phrase": confirmation_phrase,
        "danger_text": danger_text,
        "expires_at_unix": expires_at_unix,
        "operation": operation,
        "pending_id": pending_id,
        "schema": "yoetz.elevated-bootstrap.danger/1",
        "target_digest": target_digest,
    }
    if provider_binding is not None:
        body["provider_binding"] = dict(provider_binding)
    return canonical_digest(body)


def prepare_pending(
    operation: ElevatedOperation,
    *,
    target_digest: str,
    provider_binding: Mapping[str, str] | None = None,
    _state: Path | None = None,
) -> PendingElevatedConsent:
    if operation not in {"vault_initialize", "provider_credential_set"}:
        raise ElevatedBootstrapError("operation_invalid")
    if operation == "provider_credential_set" and provider_binding is None:
        raise ElevatedBootstrapError("provider_binding_required")
    if operation == "vault_initialize" and provider_binding is not None:
        raise ElevatedBootstrapError("provider_binding_forbidden")
    existing = load_pending(_state=_state)
    if existing is not None and existing.expires_at_unix > int(time.time()):
        raise ElevatedBootstrapError("pending_already_active")
    now = int(time.time())
    pending_id = secrets.token_hex(32)
    phrase = _confirmation_phrase()
    text = _danger_text(operation)
    expires = now + _TTL_SECONDS
    digest = _danger_digest(
        operation=operation,
        danger_text=text,
        confirmation_phrase=phrase,
        target_digest=target_digest,
        pending_id=pending_id,
        expires_at_unix=expires,
        provider_binding=provider_binding,
    )
    pending = PendingElevatedConsent(
        pending_id=pending_id,
        operation=operation,
        danger_text=text,
        danger_digest=digest,
        confirmation_phrase=phrase,
        created_at_unix=now,
        expires_at_unix=expires,
        target_digest=target_digest,
        provider_binding=dict(provider_binding) if provider_binding is not None else None,
    )
    _write_pending(pending, _state=_state)
    _audit(
        {
            "event": "prepare",
            "operation": operation,
            "pending_id": pending_id,
            "danger_digest": digest,
            "expires_at_unix": expires,
        },
        _state=_state,
    )
    return pending


def load_pending(*, _state: Path | None = None) -> PendingElevatedConsent | None:
    path = pending_path(_state=_state)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ElevatedBootstrapError("pending_corrupt") from exc
    if not isinstance(raw, dict):
        raise ElevatedBootstrapError("pending_corrupt")
    source = raw
    try:
        operation = source["operation"]
        if operation not in {"vault_initialize", "provider_credential_set"}:
            raise ElevatedBootstrapError("pending_corrupt")
        binding_raw = source.get("provider_binding")
        binding: dict[str, str] | None
        if binding_raw is None:
            binding = None
        elif isinstance(binding_raw, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in binding_raw.items()
        ):
            binding = {str(k): str(v) for k, v in binding_raw.items()}
        else:
            raise ElevatedBootstrapError("pending_corrupt")
        pending = PendingElevatedConsent(
            pending_id=str(source["pending_id"]),
            operation=operation,  # type: ignore[arg-type]
            danger_text=str(source["danger_text"]),
            danger_digest=str(source["danger_digest"]),
            confirmation_phrase=str(source["confirmation_phrase"]),
            created_at_unix=int(source["created_at_unix"]),
            expires_at_unix=int(source["expires_at_unix"]),
            target_digest=str(source["target_digest"]),
            provider_binding=binding,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ElevatedBootstrapError("pending_corrupt") from exc
    expected = _danger_digest(
        operation=pending.operation,
        danger_text=pending.danger_text,
        confirmation_phrase=pending.confirmation_phrase,
        target_digest=pending.target_digest,
        pending_id=pending.pending_id,
        expires_at_unix=pending.expires_at_unix,
        provider_binding=pending.provider_binding,
    )
    if expected != pending.danger_digest:
        raise ElevatedBootstrapError("pending_tampered")
    if pending.expires_at_unix <= int(time.time()):
        clear_pending(_state=_state)
        _audit(
            {"event": "expire", "pending_id": pending.pending_id, "operation": pending.operation},
            _state=_state,
        )
        return None
    return pending


def clear_pending(*, _state: Path | None = None) -> None:
    path = pending_path(_state=_state)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ElevatedBootstrapError("pending_clear_failed") from exc


def approve_pending(
    *,
    pending_id: str,
    danger_digest: str,
    confirm: str,
    _state: Path | None = None,
) -> PendingElevatedConsent:
    pending = load_pending(_state=_state)
    if pending is None:
        raise ElevatedBootstrapError("pending_absent")
    if pending.pending_id != pending_id:
        raise ElevatedBootstrapError("pending_id_mismatch")
    if pending.danger_digest != danger_digest:
        raise ElevatedBootstrapError("danger_digest_mismatch")
    if confirm != pending.confirmation_phrase:
        raise ElevatedBootstrapError("confirmation_mismatch")
    _audit(
        {
            "event": "approve_accepted",
            "pending_id": pending.pending_id,
            "operation": pending.operation,
            "danger_digest": pending.danger_digest,
        },
        _state=_state,
    )
    return pending


def read_secret_fd(fd: int, *, maximum: int) -> bytearray:
    """Read one secret from an inherited FD; requires EOF; never uses 0/1/2."""

    if type(fd) is not int or fd in {0, 1, 2} or fd < 0:
        raise ElevatedBootstrapError("secret_fd_invalid")
    if type(maximum) is not int or maximum <= 0:
        raise ElevatedBootstrapError("secret_bound_invalid")
    storage = bytearray(maximum + 1)
    used = 0
    try:
        while used < len(storage):
            view = memoryview(storage)[used:]
            try:
                count = os.readv(fd, [view])
            finally:
                view.release()
            if count == 0:
                break
            used += count
        if used == 0:
            raise ElevatedBootstrapError("secret_empty")
        if used > maximum:
            raise ElevatedBootstrapError("secret_too_large")
        if storage[used - 1] in {10, 13}:
            used -= 1
            if used == 0:
                raise ElevatedBootstrapError("secret_empty")
        for index in range(used, len(storage)):
            storage[index] = 0
        del storage[used:]
        return storage
    except ElevatedBootstrapError:
        _overwrite(storage)
        raise
    except OSError as exc:
        _overwrite(storage)
        raise ElevatedBootstrapError("secret_fd_read_failed") from exc


def projection_for_status(pending: PendingElevatedConsent | None) -> dict[str, JsonValue]:
    """Structural status projection for agents — includes danger_text for human review."""

    if pending is None:
        return {
            "required": False,
            "state": "not_prepared",
            "operation": None,
            "pending_id": None,
            "danger_digest": None,
            "confirmation_phrase": None,
            "forbidden_channels": ["mcp", "argv", "env", "stdin", "config", "transcript"],
        }
    approve = [
        "yoetz",
        "elevated-bootstrap",
        "approve",
        "--pending-id",
        pending.pending_id,
        "--danger-digest",
        pending.danger_digest,
        "--confirm",
        pending.confirmation_phrase,
    ]
    if pending.operation == "vault_initialize":
        approve.extend(["--passphrase-fd", "3"])
    else:
        approve.extend(["--reauth-fd", "3", "--credential-fd", "4"])
    return {
        "required": True,
        "state": "pending",
        "operation": pending.operation,
        "pending_id": pending.pending_id,
        "danger_digest": pending.danger_digest,
        "confirmation_phrase": pending.confirmation_phrase,
        "danger_text": pending.danger_text,
        "expires_at_unix": pending.expires_at_unix,
        "approve_command": approve,
        "forbidden_channels": ["mcp", "argv", "env", "stdin", "config", "transcript"],
    }


def status_payload(*, _state: Path | None = None) -> dict[str, JsonValue]:
    return {
        "schema": "yoetz.elevated-bootstrap.status/1",
        "elevated_bootstrap": projection_for_status(load_pending(_state=_state)),
    }


def _write_pending(pending: PendingElevatedConsent, *, _state: Path | None) -> None:
    path = pending_path(_state=_state)
    ensure_owner_only_dir(path.parent)
    payload = canonical_encode(pending.as_json())
    tmp = path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(tmp, flags, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _audit(event: Mapping[str, JsonValue], *, _state: Path | None) -> None:
    path = audit_path(_state=_state)
    ensure_owner_only_dir(path.parent)
    line = canonical_encode({**dict(event), "at_unix": int(time.time())}) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def _overwrite(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0
