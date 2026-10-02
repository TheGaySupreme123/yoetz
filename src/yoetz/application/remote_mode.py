"""Local remote-mode record (ADR-033).

This module stores an owner-supplied endpoint on disk and reports it. It never opens a socket,
imports a network client, or writes a credential. ``connect`` fails closed because remote
forwarding is still deferred.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlsplit

from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "REMOTE_MODE_SCHEMA",
    "RemoteModeError",
    "RemoteTransport",
    "configure_remote",
    "connect_remote",
    "default_remote_root",
    "disconnect_remote",
    "remote_status",
    "render_remote_status",
]

REMOTE_MODE_SCHEMA: Final = "yoetz.remote-mode/1"
_DOCUMENT_NAME: Final = "remote-mode.json"
_PRIVATE_FILE_MODE: Final = 0o600
_PRIVATE_DIR_MODE: Final = 0o700
_MAX_ENDPOINT_CHARS: Final = 253
RemoteTransport = Literal["https", "ssh"]


@dataclass(frozen=True, slots=True)
class RemoteModeError(Exception):
    """A closed local reason. The message is the reason token and nothing else."""

    reason: str

    def __str__(self) -> str:
        return self.reason


def default_remote_root() -> Path:
    """Return the owner-only directory that holds the local remote-mode record."""

    from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir

    root = state_dir() / "remote-mode"
    try:
        ensure_owner_only_dir(root)
    except PathSafetyError as exc:
        raise RemoteModeError("remote_state_invalid") from exc
    return root


def remote_status(root: Path) -> dict[str, JsonValue]:
    """Return the local projection. ``service`` stays ``local`` until forwarding exists."""

    record = _read_record(root)
    if record is None:
        return _status(transport=None, endpoint=None, credential_kind=None)
    return _status(
        transport=record["transport"],
        endpoint=record["endpoint"],
        credential_kind=record["credential_kind"],
    )


def configure_remote(
    root: Path,
    *,
    transport: str,
    endpoint: str,
    credential_kind: str = "api_key",
) -> dict[str, JsonValue]:
    """Record an endpoint. The credential kind is a label; the secret is not accepted."""

    if credential_kind != "api_key":
        raise RemoteModeError("remote_credential_unsupported")
    if transport not in {"https", "ssh"}:
        raise RemoteModeError("remote_endpoint_invalid")
    normalized = _normalize_endpoint(transport, endpoint)
    _prepare_root(root)
    body: dict[str, JsonValue] = {
        "credential_kind": "api_key",
        "endpoint": normalized,
        "schema": REMOTE_MODE_SCHEMA,
        "transport": transport,
    }
    _write_record(root, body)
    return _status(transport=transport, endpoint=normalized, credential_kind="api_key")


def disconnect_remote(root: Path) -> dict[str, JsonValue]:
    """Remove the local record. Repeated calls leave the installation in local mode."""

    path = _document_path(root)
    if path.is_symlink():
        raise RemoteModeError("remote_state_invalid")
    if path.exists():
        path.unlink()
    return remote_status(root)


def connect_remote(root: Path) -> dict[str, JsonValue]:
    """Refuse forwarding. This function opens no socket and reads no record."""

    del root
    raise RemoteModeError("remote_egress_not_authorized")


def render_remote_status(status: dict[str, JsonValue]) -> str:
    """Render the status a local human sees. Forwarding is always no."""

    transport = status.get("transport")
    endpoint = status.get("endpoint")
    lines = [
        "Service: local",
        "Forwarding: no",
        f"Transport: {transport if transport is not None else 'none'}",
        f"Endpoint: {endpoint if endpoint is not None else 'none'}",
        f"Credential kind: {status.get('credential_kind') or 'none'}",
    ]
    return "\n".join(lines)


def _status(
    *,
    transport: str | None,
    endpoint: str | None,
    credential_kind: str | None,
) -> dict[str, JsonValue]:
    return {
        "credential_kind": credential_kind,
        "endpoint": endpoint,
        "forwarding": False,
        "schema": REMOTE_MODE_SCHEMA,
        "service": "local",
        "transport": transport,
    }


def _normalize_endpoint(transport: str, endpoint: str) -> str:
    if type(endpoint) is not str or not endpoint or len(endpoint) > _MAX_ENDPOINT_CHARS:
        raise RemoteModeError("remote_endpoint_invalid")
    if any(ord(character) <= 32 or ord(character) == 127 for character in endpoint):
        raise RemoteModeError("remote_endpoint_invalid")
    if transport == "https":
        return _normalize_https(endpoint)
    return _normalize_ssh(endpoint)


def _normalize_https(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError as exc:
        raise RemoteModeError("remote_endpoint_invalid") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or not _dns_label(host)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RemoteModeError("remote_endpoint_invalid")
    if port is None:
        return f"https://{host}"
    return f"https://{host}:{port}"


def _normalize_ssh(endpoint: str) -> str:
    if "/" in endpoint or "\\" in endpoint or endpoint.startswith("-"):
        raise RemoteModeError("remote_endpoint_invalid")
    user, separator, hostport = endpoint.rpartition("@")
    if separator and (not user or "@" in user or ":" in user):
        raise RemoteModeError("remote_endpoint_invalid")
    host, port = _split_host_port(hostport)
    if not _dns_label(host):
        raise RemoteModeError("remote_endpoint_invalid")
    target = host if port is None else f"{host}:{port}"
    if user:
        return f"{user}@{target}"
    return target


def _split_host_port(hostport: str) -> tuple[str, str | None]:
    if hostport.count(":") > 1:
        raise RemoteModeError("remote_endpoint_invalid")
    if ":" not in hostport:
        return hostport, None
    host, port = hostport.split(":", 1)
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise RemoteModeError("remote_endpoint_invalid")
    return host, str(int(port))


def _dns_label(host: str) -> bool:
    if not host or len(host) > 253:
        return False
    labels = host.split(".")
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"
        if any(character not in alphabet for character in label):
            return False
    return True


def _required_string(record: Mapping[str, JsonValue], key: str) -> str:
    value = record[key]
    if isinstance(value, str):
        return value
    raise RemoteModeError("remote_state_invalid")


def _document_path(root: Path) -> Path:
    return root / _DOCUMENT_NAME


def _prepare_root(root: Path) -> None:
    if root.is_symlink():
        raise RemoteModeError("remote_state_invalid")
    root.mkdir(mode=_PRIVATE_DIR_MODE, parents=True, exist_ok=True)
    os.chmod(root, _PRIVATE_DIR_MODE)
    if stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise RemoteModeError("remote_state_invalid")


def _write_record(root: Path, body: Mapping[str, JsonValue]) -> None:
    path = _document_path(root)
    if path.is_symlink():
        raise RemoteModeError("remote_state_invalid")
    temporary = root / f".{_DOCUMENT_NAME}.tmp"
    if temporary.is_symlink():
        raise RemoteModeError("remote_state_invalid")
    payload = canonical_encode(body)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, _PRIVATE_FILE_MODE)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(temporary, _PRIVATE_FILE_MODE)
    os.replace(temporary, path)
    os.chmod(path, _PRIVATE_FILE_MODE)


def _read_record(root: Path) -> dict[str, str] | None:
    if root.is_symlink():
        raise RemoteModeError("remote_state_invalid")
    path = _document_path(root)
    if path.is_symlink():
        raise RemoteModeError("remote_state_invalid")
    if not path.exists():
        return None
    if (
        not root.is_dir()
        or not path.is_file()
        or stat.S_IMODE(root.stat().st_mode) & 0o077
        or stat.S_IMODE(path.stat().st_mode) & 0o077
    ):
        raise RemoteModeError("remote_state_invalid")
    try:
        parsed = strict_json_parse(path.read_bytes())
    except (OSError, ProtocolValueError, ValueError) as exc:
        raise RemoteModeError("remote_state_invalid") from exc
    if not isinstance(parsed, dict):
        raise RemoteModeError("remote_state_invalid")
    record = parsed
    required = {"schema", "transport", "endpoint", "credential_kind"}
    if set(record) != required:
        raise RemoteModeError("remote_state_invalid")
    schema = _required_string(record, "schema")
    transport = _required_string(record, "transport")
    endpoint = _required_string(record, "endpoint")
    credential_kind = _required_string(record, "credential_kind")
    if schema != REMOTE_MODE_SCHEMA:
        raise RemoteModeError("remote_state_invalid")
    if credential_kind != "api_key" or transport not in {"https", "ssh"}:
        raise RemoteModeError("remote_state_invalid")
    normalized = _normalize_endpoint(transport, endpoint)
    if normalized != endpoint:
        raise RemoteModeError("remote_state_invalid")
    return {"transport": transport, "endpoint": endpoint, "credential_kind": credential_kind}
