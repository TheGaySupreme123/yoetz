"""Durable, payload-free record of the route each host's MCP bridge last served.

The bridge process (``yoetz mcp serve``) is the one process that knows its serving route
first-hand from its own argv. Hooks and status surfaces that need to say "the route this host
reaches Yoetz through can dispatch AI-powered review" must not shell out to a host CLI inside
their budget, and a host registration file read from a hook is a guess about which process the
host actually launched. This store lets the bridge leave that fact behind at startup so a later
hook can read it cheaply (issue #857).

The record carries only closed tokens: the host profile, the route profile, and a second-
precision UTC timestamp. It is a snapshot of the last bridge start for that host on this
machine, not a live guarantee; a host session that outlives a re-registration keeps serving the
route it started with until the host restarts the bridge.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Literal, cast

from yoetz.config.paths import PathSafetyError, ensure_owner_only_dir, state_dir
from yoetz.protocol.canonical import JsonValue, canonical_encode

__all__ = ["SERVING_ROUTE_HOSTS", "ServingRouteHost", "read_serving_route", "record_serving_route"]

type ServingRouteHost = Literal["claude", "codex", "cursor"]
type ServingRouteProfile = Literal["policy", "strict"]

SERVING_ROUTE_HOSTS: Final[tuple[ServingRouteHost, ...]] = ("claude", "codex", "cursor")
_PROFILES: Final = frozenset({"policy", "strict"})
_SCHEMA: Final = "yoetz.serving-routes/1"
_STORE_DIRNAME: Final = "integrations"
_STORE_NAME: Final = "serving-routes.json"
_LOCK_NAME: Final = "serving-routes.lock"
_MAX_STORE_BYTES: Final = 4 * 1024


def _store_path(root: Path | None) -> Path:
    return (state_dir() if root is None else root) / _STORE_DIRNAME / _STORE_NAME


def _timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_document(raw: bytes) -> dict[str, JsonValue] | None:
    if len(raw) > _MAX_STORE_BYTES:
        return None
    try:
        loaded: object = json.loads(raw.decode("utf-8"))
    except UnicodeError, ValueError:
        return None
    if type(loaded) is not dict:
        return None
    document = cast(dict[str, JsonValue], loaded)
    if document.get("schema") != _SCHEMA or type(document.get("hosts")) is not dict:
        return None
    return document


def _read_document(path: Path) -> dict[str, JsonValue] | None:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        facts = os.fstat(descriptor)
        if not stat.S_ISREG(facts.st_mode) or facts.st_uid != os.geteuid():
            return None
        raw = os.read(descriptor, _MAX_STORE_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    return _parse_document(raw)


def read_serving_route(host: str, *, _state: Path | None = None) -> ServingRouteProfile | None:
    """Return the route profile the named host's bridge last recorded, or ``None`` if unread.

    ``None`` means the fact is unavailable (no record, unreadable, invalid, or an unknown
    host); it never means "strict". Callers that gate on a policy route must treat ``None`` as
    "route unobserved".
    """

    if host not in SERVING_ROUTE_HOSTS:
        return None
    document = _read_document(_store_path(_state))
    if document is None:
        return None
    hosts = cast(Mapping[str, JsonValue], document["hosts"])
    entry = hosts.get(host)
    if not isinstance(entry, Mapping):
        return None
    profile = cast(Mapping[str, JsonValue], entry).get("route_profile")
    if profile not in _PROFILES:
        return None
    return cast(ServingRouteProfile, profile)


def _write_private_atomic(path: Path, encoded: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(12).hex()}.tmp")
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
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def record_serving_route(host: str, route_profile: str, *, _state: Path | None = None) -> bool:
    """Record that ``host``'s bridge is serving ``route_profile``; return whether it was written.

    Fail-soft by contract: a generic host, an unknown profile, an unsafe state directory, or any
    filesystem failure returns ``False`` and never raises, because recording this fact must not
    keep a bridge from serving.
    """

    if host not in SERVING_ROUTE_HOSTS or route_profile not in _PROFILES:
        return False
    path = _store_path(_state)
    lock_path = path.with_name(_LOCK_NAME)
    descriptor: int | None = None
    try:
        ensure_owner_only_dir(path.parent)
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        facts = os.fstat(descriptor)
        if (
            not stat.S_ISREG(facts.st_mode)
            or facts.st_uid != os.geteuid()
            or stat.S_IMODE(facts.st_mode) & 0o077
        ):
            return False
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        current = _read_document(path)
        hosts: dict[str, JsonValue] = (
            dict(cast(Mapping[str, JsonValue], current["hosts"])) if current is not None else {}
        )
        hosts[host] = {"recorded_at": _timestamp(), "route_profile": route_profile}
        document: dict[str, JsonValue] = {
            "hosts": {key: hosts[key] for key in sorted(hosts) if key in SERVING_ROUTE_HOSTS},
            "schema": _SCHEMA,
        }
        encoded = canonical_encode(document) + b"\n"
        if len(encoded) > _MAX_STORE_BYTES:
            return False
        _write_private_atomic(path, encoded)
        return True
    except OSError, PathSafetyError, ValueError:
        return False
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
