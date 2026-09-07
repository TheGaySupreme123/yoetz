"""Digest-bound, explicit Cursor project MCP configuration lifecycle.

Cursor supplies project roots for project-managed servers. This adapter owns only the exact
``yoetz`` entry; it never infers repository authority from a working directory or hook record.
"""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from yoetz.adapters.integrations.launcher import valid_launcher
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)

type Route = Literal["policy", "strict"]

_MAX_BYTES = 262144
_REASONS = frozenset(
    {
        "cursor_project_mcp_target_unsafe",
        "cursor_project_mcp_config_invalid",
        "cursor_project_mcp_foreign_present",
        "cursor_project_mcp_multiple_sources",
        "cursor_project_mcp_external_source",
        "cursor_project_mcp_preview_stale",
        "cursor_project_mcp_preview_required",
        "cursor_project_mcp_command_invalid",
        "cursor_project_mcp_launcher_invalid",
        "cursor_project_mcp_write_failed",
        "cursor_project_mcp_platform_unsupported",
    }
)


class CursorProjectMcpError(ValueError):
    """A closed reason token, never host configuration or operating-system error text."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _REASONS else "cursor_project_mcp_config_invalid"
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class CursorProjectMcpTarget:
    project_root: Path
    cursor_config_root: Path


def _fail(reason: str) -> CursorProjectMcpError:
    return CursorProjectMcpError("cursor_project_mcp_" + reason)


def _path(path: object) -> None:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise _fail("target_unsafe")
    text = str(path)
    if len(text) > 4096 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise _fail("target_unsafe")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _owned(info: os.stat_result) -> None:
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise _fail("target_unsafe")


@contextmanager
def _directory(path: Path, *, optional: bool = False) -> Generator[int | None]:
    """Walk absolute components through pinned descriptors, rejecting every symlink."""
    _path(path)
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise _fail("platform_unsupported")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
            except FileNotFoundError:
                if optional:
                    yield None
                    return
                raise _fail("target_unsafe") from None
            os.close(descriptor)
            descriptor = child
        _owned(os.fstat(descriptor))
        yield descriptor
    except OSError:
        raise _fail("target_unsafe") from None
    finally:
        os.close(descriptor)


def _read_at(parent: int, name: str) -> bytes | None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError:
        raise _fail("target_unsafe") from None
    try:
        before = os.fstat(descriptor)
        _owned(before)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise _fail("target_unsafe")
        if before.st_size > _MAX_BYTES:
            raise _fail("config_invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(_MAX_BYTES + 1)
        after = os.fstat(descriptor)
        before_stamp = (*_identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        after_stamp = (*_identity(after), after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if (
            before_stamp != after_stamp
            or len(raw) != after.st_size
            or len(raw) > _MAX_BYTES
            or _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(after)
        ):
            raise _fail("preview_stale")
        return raw
    except OSError:
        raise _fail("target_unsafe") from None
    finally:
        os.close(descriptor)


def _read(path: Path) -> bytes | None:
    with _directory(path.parent, optional=True) as parent:
        return None if parent is None else _read_at(parent, path.name)


def _document(raw: bytes | None) -> dict[str, JsonValue]:
    if raw is None:
        return {}
    try:
        parsed = strict_json_parse(raw)
        if not isinstance(parsed, Mapping):
            raise _fail("config_invalid")
        document = dict(parsed)
        if "mcpServers" in document and not isinstance(document["mcpServers"], Mapping):
            raise _fail("config_invalid")
        return document
    except TypeError, ValueError:
        raise _fail("config_invalid") from None


def _entry(document: Mapping[str, JsonValue]) -> JsonValue:
    servers = document.get("mcpServers", {})
    assert isinstance(servers, Mapping)
    # Null is a same-name foreign entry, not absence.
    return servers.get("yoetz")


def _present(document: Mapping[str, JsonValue]) -> bool:
    servers = document.get("mcpServers", {})
    assert isinstance(servers, Mapping)
    return "yoetz" in servers


def _expected(launcher: tuple[str, ...], root: str | None, route: Route) -> dict[str, JsonValue]:
    if not valid_launcher(launcher):
        raise _fail("launcher_invalid")
    if root is not None:
        if type(root) is not str or str(Path(root)) != root or "\x00" in root:
            raise _fail("target_unsafe")
        with _directory(Path(root)):
            pass
    args: list[JsonValue] = [*launcher[1:], "mcp", "serve", "--host", "cursor"]
    if route == "strict":
        args.extend(("--semantic", "off"))
    entry: dict[str, JsonValue] = {"type": "stdio", "command": launcher[0], "args": args}
    if root is not None:
        entry["env"] = {"YOETZ_ISOLATED_ROOT": root}
    return entry


def _digest(raw: bytes | None) -> str:
    return (
        canonical_digest({"absent": True})
        if raw is None
        else "sha256:" + hashlib.sha256(raw).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class _Inspection:
    raws: tuple[bytes | None, ...]
    document: dict[str, JsonValue]
    target_identity: str
    state: str
    source: str
    route: Route | None


def _inspect(
    target: CursorProjectMcpTarget, launcher: tuple[str, ...], root: str | None
) -> _Inspection:
    policy, strict = _expected(launcher, root, "policy"), _expected(launcher, root, "strict")
    identities: list[JsonValue] = []
    for path in (target.project_root, target.cursor_config_root):
        with _directory(path) as descriptor:
            assert descriptor is not None
            identities.append([str(path), *_identity(os.fstat(descriptor))])
    raws = tuple(
        _read(path)
        for path in (
            target.project_root / ".cursor" / "mcp.json",
            target.cursor_config_root / "mcp.json",
            target.cursor_config_root / "plugins" / "local" / "yoetz" / "mcp.json",
        )
    )
    documents = tuple(_document(raw) for raw in raws)
    present = [i for i, document in enumerate(documents) if _present(document)]
    source = ("project", "user", "plugin")[present[0]] if len(present) == 1 else "none"
    route: Route | None = None
    if not present:
        state = "absent"
    elif len(present) > 1:
        state = "multiple_sources"
    elif source != "project":
        state = "external_source"
    else:
        entry = _entry(documents[0])
        route = "policy" if entry == policy else "strict" if entry == strict else None
        state = "yoetz_owned" if route is not None else "foreign_present"
    return _Inspection(raws, documents[0], canonical_digest(identities), state, source, route)


def status_cursor_project_mcp(
    target: CursorProjectMcpTarget,
    *,
    launcher: tuple[str, ...],
    isolation_root: str | None,
) -> dict[str, JsonValue]:
    observed = _inspect(target, launcher, isolation_root)
    return {
        "ok": True,
        "state": observed.state,
        "source": observed.source,
        "route_profile": observed.route,
        "target_identity": observed.target_identity,
        "host_trust": "unknown",
        "runtime_binding": "unobserved",
    }


def _plan(
    target: CursorProjectMcpTarget,
    action: str,
    launcher: tuple[str, ...],
    route_profile: Route | None,
    isolation_root: str | None,
) -> tuple[dict[str, JsonValue], _Inspection, bytes | None]:
    if action not in {"install", "remove"} or route_profile not in {None, "policy", "strict"}:
        raise _fail("command_invalid")
    observed = _inspect(target, launcher, isolation_root)
    if observed.state not in {"absent", "yoetz_owned"}:
        raise _fail(observed.state)
    route = route_profile or observed.route or "policy"
    document = dict(observed.document)
    servers = dict(cast(Mapping[str, JsonValue], document.get("mcpServers", {})))
    replacement = observed.raws[0]
    mutation = "noop"
    if action == "remove":
        if observed.state == "yoetz_owned":
            del servers["yoetz"]
            mutation = "unregister"
    else:
        entry = _expected(launcher, isolation_root, route)
        if servers.get("yoetz") != entry:
            servers["yoetz"] = entry
            mutation = "register" if observed.state == "absent" else "reregister"
    if mutation != "noop":
        document["mcpServers"] = servers
        replacement = canonical_encode(document) + b"\n"
        if len(replacement) > _MAX_BYTES:
            raise _fail("config_invalid")
    body: dict[str, JsonValue] = {
        "ok": True,
        "action": mutation,
        "operation": action,
        "state_before": observed.state,
        "source": observed.source,
        "route_profile": route if action == "install" else observed.route,
        "target_identity": observed.target_identity,
        "launcher": list(launcher),
        "isolated_root": isolation_root,
        "config_digest_before": _digest(observed.raws[0]),
        "config_digest_after": _digest(replacement),
        "source_digests": [_digest(raw) for raw in observed.raws],
        "warnings": ["host_config_not_compare_and_swap", "host_restart_required"],
    }
    body["preview_digest"] = canonical_digest(body)
    return body, observed, replacement


def preview_cursor_project_mcp(
    target: CursorProjectMcpTarget,
    *,
    action: str,
    launcher: tuple[str, ...],
    route_profile: Route | None,
    isolation_root: str | None,
) -> dict[str, JsonValue]:
    return _plan(target, action, launcher, route_profile, isolation_root)[0]


def _write(target: CursorProjectMcpTarget, before: bytes | None, after: bytes) -> None:
    """Pinned-parent atomic replacement with a final preimage check; no host-wide CAS claim."""
    with _directory(target.project_root) as root:
        assert root is not None
        try:
            os.mkdir(".cursor", 0o700, dir_fd=root)
        except FileExistsError:
            pass
        descriptor = os.open(".cursor", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        temporary = ".yoetz-mcp-" + uuid.uuid4().hex
        try:
            _owned(os.fstat(descriptor))
            if _read_at(descriptor, "mcp.json") != before:
                raise _fail("preview_stale")
            output = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=descriptor,
            )
            with os.fdopen(output, "wb") as stream:
                stream.write(after)
                stream.flush()
                os.fsync(stream.fileno())
            with _directory(target.project_root / ".cursor") as current:
                assert current is not None
                if (
                    _identity(os.fstat(current)) != _identity(os.fstat(descriptor))
                    or _read_at(descriptor, "mcp.json") != before
                ):
                    raise _fail("preview_stale")
                os.replace(temporary, "mcp.json", src_dir_fd=descriptor, dst_dir_fd=descriptor)
                os.fsync(descriptor)
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
            os.close(descriptor)


def apply_cursor_project_mcp(
    target: CursorProjectMcpTarget,
    *,
    action: str,
    launcher: tuple[str, ...],
    route_profile: Route | None,
    isolation_root: str | None,
    preview_digest: str,
    accept: bool,
) -> dict[str, JsonValue]:
    if accept is not True:
        raise _fail("preview_required")
    body, observed, replacement = _plan(target, action, launcher, route_profile, isolation_root)
    if preview_digest != body["preview_digest"]:
        raise _fail("preview_stale")
    if body["action"] != "noop":
        assert replacement is not None
        # Recheck every known source immediately before the project-file effect.
        if _inspect(target, launcher, isolation_root) != observed:
            raise _fail("preview_stale")
        try:
            _write(target, observed.raws[0], replacement)
        except OSError:
            raise _fail("write_failed") from None
    try:
        after = _inspect(target, launcher, isolation_root)
    except CursorProjectMcpError:
        raise _fail("write_failed") from None
    expected_state = "absent" if action == "remove" else "yoetz_owned"
    if (
        after.raws[0] != replacement
        or after.state != expected_state
        or after.target_identity != observed.target_identity
        or (
            action == "install"
            and (after.source != "project" or after.route != body["route_profile"])
        )
    ):
        raise _fail("write_failed")
    return {
        **body,
        "state_after": after.state,
        "host_trust": "unknown",
        "runtime_binding": "unobserved",
    }
