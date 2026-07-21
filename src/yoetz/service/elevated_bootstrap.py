"""Human-review consent for non-default actions (ADR-015 / ADR-016).

Pending consent is owner-only file state. Secrets enter only through inherited FDs on approve.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from yoetz.config.paths import ensure_owner_only_dir, state_dir
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode

__all__ = [
    "CONSENT_OPERATIONS",
    "ConsentOperationSpec",
    "ElevatedBootstrapError",
    "ElevatedOperation",
    "PendingElevatedConsent",
    "RiskClass",
    "approve_pending",
    "catalog_payload",
    "clear_pending",
    "load_pending",
    "operation_spec",
    "prepare_pending",
    "projection_for_status",
    "read_secret_fd",
    "status_payload",
]

RiskClass = Literal[
    "default_safe",
    "secret_ingress",
    "secret_reauth",
    "phrase_only",
    "privacy_widen",
]

ElevatedOperation = Literal[
    "vault_initialize",
    "provider_credential_set",
    "provider_credential_rotate",
    "idle_relock_disable",
    "privacy_policy_widen",
    "backup_execute",
    "restore_execute",
    "migrate_execute",
    "skill_install",
    "harness_mcp_register",
]

_SCHEMA: Final = "yoetz.elevated-bootstrap.pending/1"
_TTL_SECONDS: Final = 15 * 60
_PHRASE_BYTES: Final = 3
_PENDING_NAME: Final = "elevated-bootstrap-pending.json"
_AUDIT_NAME: Final = "elevated-bootstrap-audit.jsonl"
_FORBIDDEN: Final = ("mcp", "argv", "env", "stdin", "config", "transcript")


@dataclass(frozen=True, slots=True)
class ConsentOperationSpec:
    """One catalogued non-default (or default-safe reference) operation."""

    operation: str
    risk_class: RiskClass
    summary: str
    danger_text: str
    requires_provider_binding: bool
    requires_target_digest_arg: bool
    secret_fds: tuple[str, ...]
    implemented: bool


CONSENT_OPERATIONS: Final[tuple[ConsentOperationSpec, ...]] = (
    ConsentOperationSpec(
        operation="vault_initialize",
        risk_class="secret_ingress",
        summary="Create the local vault passphrase (cloud/no-TTY).",
        danger_text=(
            "DANGER — vault initialize. Creates this installation's vault passphrase without a "
            "user-owned /dev/tty. After you confirm the phrase, the agent supplies the passphrase "
            "on an inherited FD only. Never paste the passphrase into chat. Prefer "
            "`yoetz service initialize-passphrase` on a local terminal when possible."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=False,
        secret_fds=("passphrase-fd",),
        implemented=True,
    ),
    ConsentOperationSpec(
        operation="provider_credential_set",
        risk_class="secret_ingress",
        summary="Store an LLM API credential in the vault.",
        danger_text=(
            "DANGER — provider credential set. Stores an API credential in the local vault without "
            "a user-owned /dev/tty. After you confirm, the agent supplies reauth and credential "
            "bytes on inherited FDs only. Never paste API keys into chat."
        ),
        requires_provider_binding=True,
        requires_target_digest_arg=False,
        secret_fds=("reauth-fd", "credential-fd"),
        implemented=True,
    ),
    ConsentOperationSpec(
        operation="provider_credential_rotate",
        risk_class="secret_ingress",
        summary="Rotate a stored LLM API credential.",
        danger_text=(
            "DANGER — provider credential rotate. Replaces a stored API credential without a "
            "user-owned /dev/tty. After you confirm, reauth and new credential bytes arrive on "
            "inherited FDs only. Never paste API keys into chat."
        ),
        requires_provider_binding=True,
        requires_target_digest_arg=False,
        secret_fds=("reauth-fd", "credential-fd"),
        implemented=True,
    ),
    ConsentOperationSpec(
        operation="idle_relock_disable",
        risk_class="secret_reauth",
        summary="Disable idle vault relock (weakens locked-session protection).",
        danger_text=(
            "DANGER — disable idle relock. Weakens automatic vault locking. Confirm only if you "
            "intend this change; reauthentication is still required on an inherited FD."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=False,
        secret_fds=("reauth-fd",),
        implemented=False,
    ),
    ConsentOperationSpec(
        operation="privacy_policy_widen",
        risk_class="privacy_widen",
        summary="Widen standing privacy/egress policy.",
        danger_text=(
            "DANGER — privacy policy widen. Allows broader disclosure to agents or providers. "
            "Confirm the exact policy digest only if you intend this widening. Secrets still must "
            "never be pasted into chat."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=True,
        secret_fds=("reauth-fd",),
        implemented=False,
    ),
    ConsentOperationSpec(
        operation="backup_execute",
        risk_class="phrase_only",
        summary="Execute a backup plan (irreversible artifact write).",
        danger_text=(
            "DANGER — backup execute. Writes a durable backup for the exact plan digest. No secret "
            "bytes travel over chat; portable recovery secrets still use FDs in a later step when "
            "required. Confirm only if you reviewed the preview."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=True,
        secret_fds=(),
        implemented=True,
    ),
    ConsentOperationSpec(
        operation="restore_execute",
        risk_class="phrase_only",
        summary="Execute a restore plan (may switch active route).",
        danger_text=(
            "DANGER — restore execute. May replace or switch local ledger state for the exact plan "
            "digest. Confirm only if you reviewed the preview. Recovery secrets never go in chat."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=True,
        secret_fds=(),
        implemented=True,
    ),
    ConsentOperationSpec(
        operation="migrate_execute",
        risk_class="phrase_only",
        summary="Execute a storage migration plan.",
        danger_text=(
            "DANGER — migrate execute. Changes on-disk storage layout for the exact plan digest. "
            "Confirm only if you reviewed the preview and backup preflight."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=True,
        secret_fds=(),
        implemented=True,
    ),
    ConsentOperationSpec(
        operation="skill_install",
        risk_class="phrase_only",
        summary="Install or replace a harness skill bundle.",
        danger_text=(
            "DANGER — skill install/replace. Changes agent guidance files for the exact preview "
            "digest. Confirm only if you reviewed the file list and digests."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=True,
        secret_fds=(),
        implemented=True,
    ),
    ConsentOperationSpec(
        operation="harness_mcp_register",
        risk_class="phrase_only",
        summary="Register Yoetz as an MCP server in a harness config.",
        danger_text=(
            "DANGER — harness MCP registration. Mutates harness config for the exact preview "
            "digest. Confirm only if you reviewed the binary path and serve command."
        ),
        requires_provider_binding=False,
        requires_target_digest_arg=True,
        secret_fds=(),
        implemented=True,
    ),
)

_OPS: Final[dict[str, ConsentOperationSpec]] = {
    spec.operation: spec for spec in CONSENT_OPERATIONS
}


class ElevatedBootstrapError(Exception):
    """Bounded elevated-bootstrap failure with a stable reason token."""

    def __init__(self, reason: str) -> None:
        if type(reason) is not str or not reason:
            raise TypeError("elevated_reason_invalid")
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class PendingElevatedConsent:
    """One active elevated consent pending record."""

    pending_id: str
    operation: ElevatedOperation
    risk_class: RiskClass
    danger_text: str
    danger_digest: str
    confirmation_phrase: str
    created_at_unix: int
    expires_at_unix: int
    target_digest: str
    provider_binding: Mapping[str, str] | None
    secret_fds: tuple[str, ...]

    def as_json(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema": _SCHEMA,
            "pending_id": self.pending_id,
            "state": "pending",
            "operation": self.operation,
            "risk_class": self.risk_class,
            "danger_text": self.danger_text,
            "danger_digest": self.danger_digest,
            "confirmation_phrase": self.confirmation_phrase,
            "created_at_unix": self.created_at_unix,
            "expires_at_unix": self.expires_at_unix,
            "target_digest": self.target_digest,
            "secret_fds": list(self.secret_fds),
        }
        if self.provider_binding is not None:
            payload["provider_binding"] = dict(self.provider_binding)
        return payload


def operation_spec(operation: str) -> ConsentOperationSpec:
    try:
        return _OPS[operation]
    except KeyError as exc:
        raise ElevatedBootstrapError("operation_invalid") from exc


def elevated_dir(*, _state: Path | None = None) -> Path:
    root = state_dir() if _state is None else _state
    path = root / "elevated-bootstrap"
    ensure_owner_only_dir(path)
    return path


def pending_path(*, _state: Path | None = None) -> Path:
    return elevated_dir(_state=_state) / _PENDING_NAME


def audit_path(*, _state: Path | None = None) -> Path:
    return elevated_dir(_state=_state) / _AUDIT_NAME


def _confirmation_phrase() -> str:
    return f"YOETZ APPROVE {secrets.token_hex(_PHRASE_BYTES).upper()}"


def _danger_digest(
    *,
    operation: str,
    risk_class: str,
    danger_text: str,
    confirmation_phrase: str,
    target_digest: str,
    pending_id: str,
    expires_at_unix: int,
    provider_binding: Mapping[str, str] | None,
    secret_fds: Sequence[str],
) -> str:
    body: dict[str, JsonValue] = {
        "confirmation_phrase": confirmation_phrase,
        "danger_text": danger_text,
        "expires_at_unix": expires_at_unix,
        "operation": operation,
        "pending_id": pending_id,
        "risk_class": risk_class,
        "schema": "yoetz.elevated-bootstrap.danger/1",
        "secret_fds": list(secret_fds),
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
    spec = operation_spec(operation)
    if not spec.implemented:
        raise ElevatedBootstrapError("operation_not_implemented")
    if spec.requires_provider_binding and provider_binding is None:
        raise ElevatedBootstrapError("provider_binding_required")
    if not spec.requires_provider_binding and provider_binding is not None:
        raise ElevatedBootstrapError("provider_binding_forbidden")
    if type(target_digest) is not str or not target_digest.startswith("sha256:"):
        raise ElevatedBootstrapError("target_digest_invalid")
    existing = load_pending(_state=_state)
    if existing is not None and existing.expires_at_unix > int(time.time()):
        raise ElevatedBootstrapError("pending_already_active")
    now = int(time.time())
    pending_id = secrets.token_hex(32)
    phrase = _confirmation_phrase()
    text = spec.danger_text
    expires = now + _TTL_SECONDS
    digest = _danger_digest(
        operation=operation,
        risk_class=spec.risk_class,
        danger_text=text,
        confirmation_phrase=phrase,
        target_digest=target_digest,
        pending_id=pending_id,
        expires_at_unix=expires,
        provider_binding=provider_binding,
        secret_fds=spec.secret_fds,
    )
    pending = PendingElevatedConsent(
        pending_id=pending_id,
        operation=operation,
        risk_class=spec.risk_class,
        danger_text=text,
        danger_digest=digest,
        confirmation_phrase=phrase,
        created_at_unix=now,
        expires_at_unix=expires,
        target_digest=target_digest,
        provider_binding=dict(provider_binding) if provider_binding is not None else None,
        secret_fds=spec.secret_fds,
    )
    _write_pending(pending, _state=_state)
    _audit(
        {
            "event": "prepare",
            "operation": operation,
            "risk_class": spec.risk_class,
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
        operation = str(source["operation"])
        if operation not in _OPS:
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
        fds_raw = source.get("secret_fds", ())
        if not isinstance(fds_raw, list) or not all(isinstance(item, str) for item in fds_raw):
            raise ElevatedBootstrapError("pending_corrupt")
        risk = str(source.get("risk_class", _OPS[operation].risk_class))
        pending = PendingElevatedConsent(
            pending_id=str(source["pending_id"]),
            operation=operation,  # type: ignore[arg-type]
            risk_class=risk,  # type: ignore[arg-type]
            danger_text=str(source["danger_text"]),
            danger_digest=str(source["danger_digest"]),
            confirmation_phrase=str(source["confirmation_phrase"]),
            created_at_unix=int(source["created_at_unix"]),
            expires_at_unix=int(source["expires_at_unix"]),
            target_digest=str(source["target_digest"]),
            provider_binding=binding,
            secret_fds=tuple(str(item) for item in fds_raw),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ElevatedBootstrapError("pending_corrupt") from exc
    expected = _danger_digest(
        operation=pending.operation,
        risk_class=pending.risk_class,
        danger_text=pending.danger_text,
        confirmation_phrase=pending.confirmation_phrase,
        target_digest=pending.target_digest,
        pending_id=pending.pending_id,
        expires_at_unix=pending.expires_at_unix,
        provider_binding=pending.provider_binding,
        secret_fds=pending.secret_fds,
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
            "risk_class": pending.risk_class,
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


def _approve_command(pending: PendingElevatedConsent) -> list[str]:
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
    next_fd = 3
    for name in pending.secret_fds:
        approve.extend([f"--{name}", str(next_fd)])
        next_fd += 1
    return approve


def projection_for_status(pending: PendingElevatedConsent | None) -> dict[str, JsonValue]:
    """Structural status projection for agents — includes danger_text for human review."""

    if pending is None:
        return {
            "required": False,
            "state": "not_prepared",
            "operation": None,
            "risk_class": None,
            "pending_id": None,
            "danger_digest": None,
            "confirmation_phrase": None,
            "secret_fds": [],
            "forbidden_channels": list(_FORBIDDEN),
            "user_steps": [
                "If a non-default action is needed, run prepare for that operation.",
                "Show the human danger_text and ask them to repeat confirmation_phrase.",
                "Never ask for secrets in chat; use inherited FDs only when catalog requires them.",
            ],
        }
    return {
        "required": True,
        "state": "pending",
        "operation": pending.operation,
        "risk_class": pending.risk_class,
        "pending_id": pending.pending_id,
        "danger_digest": pending.danger_digest,
        "confirmation_phrase": pending.confirmation_phrase,
        "danger_text": pending.danger_text,
        "expires_at_unix": pending.expires_at_unix,
        "target_digest": pending.target_digest,
        "secret_fds": list(pending.secret_fds),
        "approve_command": _approve_command(pending),
        "forbidden_channels": list(_FORBIDDEN),
        "user_steps": [
            "Show danger_text to the human.",
            "Ask them to repeat confirmation_phrase exactly.",
            "Run only approve_command; supply secret FDs only if listed.",
        ],
    }


def catalog_payload() -> dict[str, JsonValue]:
    """Agent-facing catalog of default-safe vs consent-required operations."""

    operations: list[JsonValue] = []
    for spec in CONSENT_OPERATIONS:
        operations.append(
            {
                "operation": spec.operation,
                "risk_class": spec.risk_class,
                "summary": spec.summary,
                "implemented": spec.implemented,
                "requires_provider_binding": spec.requires_provider_binding,
                "requires_target_digest_arg": spec.requires_target_digest_arg,
                "secret_fds": list(spec.secret_fds),
                "prepare_hint": (
                    f"yoetz elevated-bootstrap prepare {spec.operation}"
                    + (" --target-digest <sha256:...>" if spec.requires_target_digest_arg else "")
                ),
            }
        )
    return {
        "schema": "yoetz.consent.catalog/1",
        "default_safe": [
            "mcp.start",
            "mcp.publish_work",
            "mcp.check",
            "mcp.respond",
            "mcp.status",
            "mcp.receipt",
            "privacy.tighten",
        ],
        "rules": {
            "never_over_chat_or_mcp": list(_FORBIDDEN),
            "no_standing_yolo": True,
            "path_safety_not_waivable_by_consent": True,
            "prefer_tty_when_available": True,
            "one_pending_at_a_time": True,
        },
        "operations": operations,
    }


def status_payload(*, _state: Path | None = None) -> dict[str, JsonValue]:
    return {
        "schema": "yoetz.elevated-bootstrap.status/1",
        "elevated_bootstrap": projection_for_status(load_pending(_state=_state)),
        "consent_catalog": catalog_payload(),
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
