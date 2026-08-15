"""Consent-gated Codex marketplace registration and plugin activation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, cast

from yoetz import __version__
from yoetz.adapters.integrations.codex_plugin import (
    PluginHookPresence,
    inspect_plugin,
    render_plugin_install_tree,
)
from yoetz.adapters.integrations.codex_session_stream import resolve_codex_home
from yoetz.ports.integrations import (
    IntegrationError,
    IntegrationReason,
    IntegrationScope,
    IntegrationTarget,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError

try:
    import fcntl
except ImportError:  # pragma: no cover - supported Codex plugin hosts are POSIX
    fcntl = None  # type: ignore[assignment]

__all__ = [
    "ActivationInspection",
    "ActivationPreview",
    "ActivationState",
    "apply_activation",
    "inspect_activation",
    "preview_activation",
    "resolve_codex_home_for_binary",
]

_MARKETPLACE_NAME: Final = "yoetz"
_PLUGIN_ID: Final = "yoetz@yoetz"
_MARKETPLACE_RELATIVE_PATH: Final = ".agents/plugins/marketplace.json"
_PLUGIN_RELATIVE_PATH: Final = "./.agents/plugins/yoetz"
_MAX_MARKETPLACE_BYTES: Final = 262_144
_MAX_CONFIG_BYTES: Final = 1_048_576
_MAX_VERSION_BYTES: Final = 4_096
_VERSION_TIMEOUT_SECONDS: Final = 5.0
_PLUGIN_ADD_TIMEOUT_SECONDS: Final = 30.0
_MAX_PLUGIN_OUTPUT_BYTES: Final = 262_144
_MAX_EXECUTABLE_BYTES: Final = 256 * 1024 * 1024
_ACTIVATION_LOCK: Final = ".yoetz-marketplace-activation.lock"
_ASYNC_PLUGIN_VARIANT_VERSION: Final = "0.148.0-alpha.6"
_PLUGIN_ADD_COMMAND: Final = ("plugin", "add", _PLUGIN_ID, "--json")
_PLUGIN_LIST_COMMAND: Final = ("plugin", "list", "--marketplace", _MARKETPLACE_NAME, "--json")
_VERSION_RE: Final = re.compile(
    r"\b(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\b", re.ASCII
)


class ActivationState(str, Enum):  # noqa: UP042 - exact structural enum
    ACTIVE = "active"
    INSTALLED_NOT_ACTIVATED = "installed_not_activated"
    NOT_INSTALLED = "not_installed"
    FOREIGN = "foreign"


@dataclass(frozen=True, slots=True)
class ActivationInspection:
    """Read-only classification of Codex marketplace and plugin activation state."""

    marketplace_registered: bool
    plugin_enabled: bool
    state: ActivationState
    plugin_cached: bool = False
    inventory_verified: bool = True


@dataclass(frozen=True, slots=True)
class ActivationPreview:
    """Exact activation bytes shown before the owner approves mutation."""

    marketplace_bytes: bytes
    config_toml_block: str
    preview_digest: str
    inspection: ActivationInspection
    plugin_source_digest: str
    codex_home: Path
    plugin_install_path: Path
    plugin_install_digest: str
    executable_path: Path
    executable_digest: str
    codex_version: str
    probe_command: tuple[str, ...]
    inventory_command: tuple[str, ...]
    install_command: tuple[str, ...]
    probe_environment: str
    activation_environment: tuple[tuple[str, str], ...]
    marketplace_preimage_digest: str
    config_preimage_digest: str
    cache_mutation_planned: bool


@dataclass(frozen=True, slots=True)
class _ActivationPlan:
    preview: ActivationPreview
    marketplace_before: bytes | None
    config_before: bytes | None
    config_after: bytes
    cache_members: Mapping[str, bytes]
    cache_before: str | None
    binary: _CodexBinaryProbe


@dataclass(frozen=True, slots=True)
class _CodexBinaryProbe:
    executable_path: Path
    executable_digest: str
    codex_version: str
    codex_home: Path


def _error(reason: IntegrationReason) -> IntegrationError:
    return IntegrationError(reason, {})


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_EXECUTABLE_BYTES:
                    raise _error(IntegrationReason.SOURCE_INVALID)
                digest.update(chunk)
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    return f"sha256:{digest.hexdigest()}"


def _probe_codex_binary(
    executable_path: str,
    *,
    codex_home: Path | str | None = None,
    _run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> _CodexBinaryProbe:
    """Bind an exact executable/version to one explicitly controlled Codex home."""

    if (
        type(executable_path) is not str
        or not executable_path
        or len(executable_path) > 4_096
        or any(ord(char) < 32 or ord(char) == 127 for char in executable_path)
    ):
        raise _error(IntegrationReason.TARGET_UNSAFE)
    executable = Path(executable_path)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise _error(IntegrationReason.TARGET_UNSAFE)
    try:
        resolved_executable = executable.resolve(strict=True)
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    executable_digest = _sha_file(resolved_executable)
    home = resolve_codex_home_for_binary(executable_path, codex_home=codex_home)
    try:
        with tempfile.TemporaryDirectory(prefix="yoetz-codex-version-") as temporary:
            probe_home = Path(temporary)
            probe_home.chmod(0o700)
            completed = _run(
                (str(executable), "--version"),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=_VERSION_TIMEOUT_SECONDS,
                check=False,
                shell=False,
                env=_codex_environment(probe_home),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error(IntegrationReason.SOURCE_INVALID) from exc
    raw = completed.stdout[: _MAX_VERSION_BYTES + 1]
    if completed.returncode != 0 or not raw or len(raw) > _MAX_VERSION_BYTES:
        raise _error(IntegrationReason.SOURCE_INVALID)
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _error(IntegrationReason.SOURCE_INVALID) from exc
    match = _VERSION_RE.search(text)
    if match is None:
        raise _error(IntegrationReason.SOURCE_INVALID)
    return _CodexBinaryProbe(
        executable.absolute(),
        executable_digest,
        match.group(1),
        home,
    )


def resolve_codex_home_for_binary(
    executable_path: str,
    *,
    codex_home: Path | str | None = None,
    _run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> Path:
    """Select the home that activation will force for the exact executable.

    This performs no subprocess or network probe. Every later Codex command receives both home
    variables set to this value, so wrappers cannot redirect the approved mutation target.
    """

    del _run
    del executable_path
    if codex_home is None:
        raise _error(IntegrationReason.TARGET_UNTRUSTED)
    return _codex_root(codex_home)


def _codex_environment(home: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["CODEX_HOME"] = str(home)
    environment["CODEX_TESTING_HOME"] = str(home)
    return environment


def _validated_project(target: IntegrationTarget) -> Path:
    if (
        type(target) is not IntegrationTarget
        or target.scope is not IntegrationScope.TRUSTED_PROJECT
    ):
        raise _error(IntegrationReason.TARGET_UNTRUSTED)
    root = Path(target.project_root)
    if not root.is_absolute():
        root = Path.cwd() / root
    if root == Path(root.anchor) or root == Path.home() or root.is_symlink() or not root.is_dir():
        raise _error(IntegrationReason.TARGET_UNTRUSTED)
    try:
        stat = root.stat()
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    if hasattr(os, "geteuid") and stat.st_uid != os.geteuid():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    if stat.st_mode & 0o022:
        raise _error(IntegrationReason.TARGET_UNSAFE)
    resolved = root.resolve()
    if root.absolute() != resolved:
        raise _error(IntegrationReason.TARGET_UNSAFE)
    return resolved


def _owned_not_writable(path: Path, *, directory: bool) -> None:
    try:
        facts = path.lstat()
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    if path.is_symlink() or (not path.is_dir() if directory else not path.is_file()):
        raise _error(IntegrationReason.TARGET_UNSAFE)
    if hasattr(os, "geteuid") and facts.st_uid != os.geteuid():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    if facts.st_mode & 0o022:
        raise _error(IntegrationReason.TARGET_UNSAFE)


def _validate_descendant_ancestors(root: Path, relative_parent: Path) -> None:
    current = root
    for part in relative_parent.parts:
        current /= part
        if not current.exists() and not current.is_symlink():
            return
        _owned_not_writable(current, directory=True)


def _codex_root(codex_home: Path | str | None) -> Path:
    root = resolve_codex_home(codex_home)
    if not root.is_absolute() or root == Path(root.anchor) or root == Path.home():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    if not root.exists() and not root.is_symlink():
        raise _error(IntegrationReason.TARGET_UNTRUSTED)
    _owned_not_writable(root, directory=True)
    resolved = root.resolve()
    if root.absolute() != resolved:
        raise _error(IntegrationReason.TARGET_UNSAFE)
    return resolved


def _marketplace_document() -> dict[str, JsonValue]:
    return {
        "name": _MARKETPLACE_NAME,
        "plugins": [
            {
                "name": "yoetz",
                "source": {"source": "local", "path": _PLUGIN_RELATIVE_PATH},
            }
        ],
    }


def _load_marketplace(path: Path) -> tuple[bytes, Mapping[str, object]] | None:
    if path.is_symlink():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    if not raw or len(raw) > _MAX_MARKETPLACE_BYTES:
        raise _error(IntegrationReason.SOURCE_INVALID)
    _owned_not_writable(path, directory=False)
    try:
        parsed = strict_json_parse(raw)
    except Exception as exc:
        raise _error(IntegrationReason.SOURCE_INVALID) from exc
    if not isinstance(parsed, Mapping):
        raise _error(IntegrationReason.SOURCE_INVALID)
    return raw, cast(Mapping[str, object], parsed)


def _expected_source() -> dict[str, str]:
    return {"source": "local", "path": _PLUGIN_RELATIVE_PATH}


def _classify_manifest(document: Mapping[str, object] | None) -> tuple[bool, bool]:
    """Return ``(registered, foreign)`` for one marketplace document."""

    if document is None:
        return False, False
    if document.get("name") != _MARKETPLACE_NAME:
        return False, True
    plugins = document.get("plugins")
    if type(plugins) is not list:
        return False, True
    registered = False
    for raw in cast(list[object], plugins):
        if not isinstance(raw, Mapping):
            return False, True
        row = cast(Mapping[str, object], raw)
        if row.get("name") != "yoetz":
            continue
        if registered or row.get("source") != _expected_source():
            return False, True
        registered = True
    return registered, False


def _personal_manifest_conflicts(document: Mapping[str, object] | None) -> bool:
    """A different personal marketplace is harmless unless it also claims ``yoetz``."""

    if document is None:
        return False
    if document.get("name") == _MARKETPLACE_NAME:
        return True
    plugins = document.get("plugins")
    if type(plugins) is not list:
        return False
    for raw in cast(list[object], plugins):
        if isinstance(raw, Mapping) and cast(Mapping[str, object], raw).get("name") == "yoetz":
            return True
    return False


def _load_config(path: Path) -> tuple[bytes, Mapping[str, object]]:
    if path.is_symlink():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return b"", {}
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    if len(raw) > _MAX_CONFIG_BYTES:
        raise _error(IntegrationReason.SOURCE_INVALID)
    _owned_not_writable(path, directory=False)
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise _error(IntegrationReason.SOURCE_INVALID) from exc
    return raw, cast(Mapping[str, object], parsed)


def _table(root: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    value = root.get(name)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    return cast(Mapping[str, object], value)


def _config_state(config: Mapping[str, object], project: Path) -> tuple[bool, bool, bool]:
    """Return ``(marketplace_configured, plugin_enabled, foreign)``."""

    marketplaces = _table(config, "marketplaces")
    marketplace = None if marketplaces is None else marketplaces.get(_MARKETPLACE_NAME)
    configured = marketplace is not None
    if marketplace is not None:
        if not isinstance(marketplace, Mapping):
            return False, False, True
        row = cast(Mapping[str, object], marketplace)
        if row.get("source_type") != "local" or row.get("source") != str(project):
            return False, False, True

    plugins = _table(config, "plugins")
    plugin = None if plugins is None else plugins.get(_PLUGIN_ID)
    enabled = False
    if plugin is not None:
        if not isinstance(plugin, Mapping):
            return configured, False, True
        row = cast(Mapping[str, object], plugin)
        flag = row.get("enabled")
        if type(flag) is not bool:
            return configured, False, True
        enabled = flag
    return configured, enabled, False


def _paths(
    target: IntegrationTarget, codex_home: Path | str | None
) -> tuple[Path, Path, Path, Path]:
    project = _validated_project(target)
    _validate_descendant_ancestors(project, Path(".agents/plugins"))
    home = _codex_root(codex_home)
    return (
        project,
        home,
        project / _MARKETPLACE_RELATIVE_PATH,
        home / "config.toml",
    )


def _cache_root(home: Path) -> Path:
    return home / "plugins" / "cache" / _MARKETPLACE_NAME / "yoetz"


def _cache_version_path(root: Path, version: str) -> Path:
    if _VERSION_RE.fullmatch(version) is None:
        raise _error(IntegrationReason.SOURCE_INVALID)
    return root / version


def _source_cache_members(target: IntegrationTarget, *, codex_version: str) -> dict[str, bytes]:
    project = _validated_project(target)
    source = project / ".agents/plugins/yoetz"
    expected = render_plugin_install_tree(codex_version=codex_version)
    inspection = inspect_plugin(target, codex_version=codex_version)
    if inspection.presence is PluginHookPresence.ABSENT:
        return expected
    if inspection.presence is not PluginHookPresence.INSTALLED:
        # Setup previews activation before it replaces a previously managed tree.
        # Admit only the other byte-exact renderer variant here, then bind the
        # preview to the intended version-specific bytes. Apply still requires
        # that intended tree at its first source fence before any mutation.
        alternate_versions = (
            (None, _ASYNC_PLUGIN_VARIANT_VERSION)
            if codex_version != _ASYNC_PLUGIN_VARIANT_VERSION
            else (None,)
        )
        if any(
            inspect_plugin(target, codex_version=alternate).presence is PluginHookPresence.INSTALLED
            for alternate in alternate_versions
        ):
            return expected
        raise _error(IntegrationReason.PARTIAL_INSTALL)
    actual_paths: set[str] = set()
    for candidate in source.rglob("*"):
        if candidate.is_symlink():
            raise _error(IntegrationReason.TARGET_UNSAFE)
        relative = candidate.relative_to(source).as_posix()
        if candidate.is_dir():
            _owned_not_writable(candidate, directory=True)
        elif candidate.is_file():
            _owned_not_writable(candidate, directory=False)
            actual_paths.add(relative)
        else:
            raise _error(IntegrationReason.TARGET_UNSAFE)
    if actual_paths != set(expected):
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    members: dict[str, bytes] = {}
    for relative_path in expected:
        path = source / relative_path
        current = source
        for part in Path(relative_path).parts:
            current /= part
            if current.is_symlink():
                raise _error(IntegrationReason.TARGET_UNSAFE)
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise _error(IntegrationReason.TARGET_UNSAFE) from exc
        _owned_not_writable(path, directory=False)
        if payload != expected[relative_path]:
            raise _error(IntegrationReason.PARTIAL_INSTALL)
        members[relative_path] = payload
    return members


def _members_digest(members: Mapping[str, bytes]) -> str:
    rows = [
        {"path": path, "digest": _sha(payload), "size": len(payload)}
        for path, payload in sorted(members.items())
    ]
    return _sha(canonical_encode(cast(JsonValue, {"managed_files": rows})))


def _installed_cache_digest(path: Path, expected_members: Mapping[str, bytes]) -> str | None:
    if path.is_symlink():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    if not path.exists():
        return None
    _owned_not_writable(path, directory=True)
    expected_paths = frozenset(expected_members)
    actual_paths: set[str] = set()
    try:
        for candidate in path.rglob("*"):
            if candidate.is_symlink():
                raise _error(IntegrationReason.TARGET_UNSAFE)
            relative = candidate.relative_to(path).as_posix()
            if candidate.is_file():
                _owned_not_writable(candidate, directory=False)
                actual_paths.add(relative)
            elif candidate.is_dir():
                _owned_not_writable(candidate, directory=True)
            else:
                raise _error(IntegrationReason.TARGET_UNSAFE)
        if actual_paths != set(expected_paths):
            raise _error(IntegrationReason.DESTINATION_CONFLICT)
        actual = {relative: (path / relative).read_bytes() for relative in sorted(actual_paths)}
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    if any(actual[name] != payload for name, payload in expected_members.items()):
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    return _members_digest(actual)


def _run_json_command(
    binary: _CodexBinaryProbe,
    command: tuple[str, ...],
    *,
    _run: Callable[..., subprocess.CompletedProcess[bytes]],
    timeout: float,
) -> Mapping[str, object]:
    try:
        completed = _run(
            (str(binary.executable_path), *command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
            env=_codex_environment(binary.codex_home),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error(IntegrationReason.WRITE_FAILED) from exc
    raw = completed.stdout[: _MAX_PLUGIN_OUTPUT_BYTES + 1]
    if completed.returncode != 0 or not raw or len(raw) > _MAX_PLUGIN_OUTPUT_BYTES:
        raise _error(IntegrationReason.WRITE_FAILED)
    try:
        parsed = strict_json_parse(raw)
    except ProtocolValueError as exc:
        raise _error(IntegrationReason.SOURCE_INVALID) from exc
    if not isinstance(parsed, Mapping):
        raise _error(IntegrationReason.SOURCE_INVALID)
    return cast(Mapping[str, object], parsed)


def _plugin_inventory(
    binary: _CodexBinaryProbe,
    project: Path,
    *,
    _run: Callable[..., subprocess.CompletedProcess[bytes]],
) -> tuple[bool, str | None]:
    """Return exact Yoetz installed state/version from canonical Codex inventory."""

    document = _run_json_command(
        binary,
        _PLUGIN_LIST_COMMAND,
        _run=_run,
        timeout=_VERSION_TIMEOUT_SECONDS,
    )
    matches: list[Mapping[str, object]] = []
    for field in ("installed", "available"):
        raw_rows = document.get(field, [])
        if type(raw_rows) is not list:
            raise _error(IntegrationReason.SOURCE_INVALID)
        for raw in cast(list[object], raw_rows):
            if not isinstance(raw, Mapping):
                raise _error(IntegrationReason.SOURCE_INVALID)
            row = cast(Mapping[str, object], raw)
            if row.get("pluginId") == _PLUGIN_ID:
                matches.append(row)
    if not matches:
        return False, None
    if len(matches) != 1:
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    row = matches[0]
    expected_source = {"source": "local", "path": str(project / ".agents/plugins/yoetz")}
    expected_marketplace = {"sourceType": "local", "source": str(project)}
    if row.get("source") != expected_source or row.get("marketplaceSource") != expected_marketplace:
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    installed = row.get("installed")
    enabled = row.get("enabled")
    version = row.get("version")
    if type(installed) is not bool or type(enabled) is not bool:
        raise _error(IntegrationReason.SOURCE_INVALID)
    if version is not None and (type(version) is not str or _VERSION_RE.fullmatch(version) is None):
        raise _error(IntegrationReason.SOURCE_INVALID)
    if installed and (enabled is not True or type(version) is not str):
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    return installed, version


def inspect_activation(
    target: IntegrationTarget,
    *,
    executable_path: str,
    codex_home: Path | str | None = None,
    _run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    _canonical_inventory: bool = True,
) -> ActivationInspection:
    """Inspect installation, repository registration, and selected Codex-home activation."""

    binary = _probe_codex_binary(executable_path, codex_home=codex_home, _run=_run)
    if codex_home is not None and _codex_root(codex_home) != binary.codex_home:
        raise _error(IntegrationReason.PREVIEW_STALE)
    project, home, marketplace_path, config_path = _paths(target, binary.codex_home)
    installed = (
        inspect_plugin(target, codex_version=binary.codex_version).presence
        is PluginHookPresence.INSTALLED
    )
    plugin_cached = False
    cache_foreign = False
    if installed:
        try:
            cache_members = _source_cache_members(target, codex_version=binary.codex_version)
            local_digest = _installed_cache_digest(
                _cache_version_path(_cache_root(home), __version__), cache_members
            )
            if _canonical_inventory:
                inventory_installed, version = _plugin_inventory(binary, project, _run=_run)
                if inventory_installed:
                    assert version is not None
                    plugin_cached = _installed_cache_digest(
                        _cache_version_path(_cache_root(home), version),
                        cache_members,
                    ) == _members_digest(cache_members)
                elif _cache_root(home).exists():
                    cache_foreign = True
            elif local_digest is not None:
                # Preview is mutation-free: a canonical inventory command is reserved for apply.
                plugin_cached = False
            elif _cache_root(home).exists():
                cache_foreign = True
        except IntegrationError as exc:
            if exc.reason in {
                IntegrationReason.DESTINATION_CONFLICT,
                IntegrationReason.TARGET_UNSAFE,
            }:
                cache_foreign = True
            else:
                raise
    repo_snapshot = _load_marketplace(marketplace_path)
    repo_document = None if repo_snapshot is None else repo_snapshot[1]
    repo_registered, repo_foreign = _classify_manifest(repo_document)

    # A same-named personal marketplace entry is ambiguous with the repository-scoped source.
    personal_path = home / _MARKETPLACE_RELATIVE_PATH
    personal_document = None
    if personal_path != marketplace_path:
        personal_snapshot = _load_marketplace(personal_path)
        personal_document = None if personal_snapshot is None else personal_snapshot[1]
    _raw, config = _load_config(config_path)
    config_registered, plugin_enabled, config_foreign = _config_state(config, project)
    foreign = (
        repo_foreign
        or _personal_manifest_conflicts(personal_document)
        or config_foreign
        or cache_foreign
    )
    marketplace_registered = repo_registered and config_registered
    if foreign:
        state = ActivationState.FOREIGN
    elif not installed:
        state = ActivationState.NOT_INSTALLED
    elif marketplace_registered and plugin_enabled and plugin_cached:
        state = ActivationState.ACTIVE
    else:
        state = ActivationState.INSTALLED_NOT_ACTIVATED
    return ActivationInspection(
        marketplace_registered,
        plugin_enabled,
        state,
        plugin_cached,
        _canonical_inventory,
    )


def _merged_marketplace_bytes(existing: Mapping[str, object] | None) -> bytes:
    if existing is None:
        document: dict[str, object] = dict(_marketplace_document())
    else:
        _registered, foreign = _classify_manifest(existing)
        if foreign:
            raise _error(IntegrationReason.DESTINATION_CONFLICT)
        document = dict(existing)
        raw_plugins = document.get("plugins", [])
        assert type(raw_plugins) is list
        plugins = [
            dict(cast(Mapping[str, object], item)) for item in cast(list[object], raw_plugins)
        ]
        if not any(item.get("name") == "yoetz" for item in plugins):
            plugins.append({"name": "yoetz", "source": _expected_source()})
        document["name"] = _MARKETPLACE_NAME
        document["plugins"] = plugins
    return canonical_encode(cast(JsonValue, document)) + b"\n"


def _toml_string(value: str) -> str:
    # ``ensure_ascii=False`` keeps non-BMP characters literal (valid in TOML basic
    # strings) instead of surrogate-pair ``\uXXXX`` escapes (invalid TOML), while
    # JSON still escapes quote, backslash, and C0 controls as TOML-valid escapes.
    # DEL is the one TOML-forbidden literal JSON leaves unescaped.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def _activation_block(project: Path, config: Mapping[str, object]) -> str:
    configured, enabled, foreign = _config_state(config, project)
    if foreign:
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    plugins = _table(config, "plugins")
    if plugins is not None and _PLUGIN_ID in plugins and not enabled:
        # The owner-authored table cannot be appended a second time and this adapter never
        # rewrites user TOML. The owner must remove the disabled table before a new preview.
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    blocks: list[str] = []
    if not configured:
        blocks.append(
            f'[marketplaces.yoetz]\nsource_type = "local"\nsource = {_toml_string(str(project))}\n'
        )
    if not enabled:
        blocks.append(f'[plugins."{_PLUGIN_ID}"]\nenabled = true\n')
    return "\n".join(blocks)


def _sha(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _plugin_source_digest(*, codex_version: str) -> str:
    rows = [
        {"path": path, "digest": _sha(payload), "size": len(payload)}
        for path, payload in sorted(render_plugin_install_tree(codex_version=codex_version).items())
    ]
    return _sha(canonical_encode(cast(JsonValue, {"managed_files": rows})))


def _activation_plan(
    target: IntegrationTarget,
    *,
    executable_path: str,
    codex_home: Path | str | None = None,
    _run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> _ActivationPlan:
    binary = _probe_codex_binary(executable_path, codex_home=codex_home, _run=_run)
    if codex_home is not None and _codex_root(codex_home) != binary.codex_home:
        raise _error(IntegrationReason.PREVIEW_STALE)
    project, home, marketplace_path, config_path = _paths(target, binary.codex_home)
    inspection = inspect_activation(
        target,
        executable_path=executable_path,
        codex_home=home,
        _run=_run,
        _canonical_inventory=False,
    )
    if inspection.state is ActivationState.FOREIGN:
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    marketplace_snapshot = _load_marketplace(marketplace_path)
    marketplace_before = None if marketplace_snapshot is None else marketplace_snapshot[0]
    marketplace_document = None if marketplace_snapshot is None else marketplace_snapshot[1]
    marketplace_bytes = _merged_marketplace_bytes(marketplace_document)
    config_bytes, config = _load_config(config_path)
    config_before = config_bytes if config_path.exists() else None
    block = _activation_block(project, config)
    plugin_source_digest = _plugin_source_digest(codex_version=binary.codex_version)
    cache_members = _source_cache_members(target, codex_version=binary.codex_version)
    plugin_install_digest = _members_digest(cache_members)
    plugin_cache_root = _cache_root(home)
    plugin_install_path = _cache_version_path(plugin_cache_root, __version__)
    cache_before = _installed_cache_digest(plugin_install_path, cache_members)
    digest_body = (
        b"yoetz.codex-marketplace-activation/2\0"
        + str(home).encode("utf-8")
        + b"\0"
        + _sha(b"" if marketplace_before is None else marketplace_before).encode()
        + b"\0"
        + (b"present:" if config_before is not None else b"absent:")
        + _sha(config_bytes).encode()
        + b"\0"
        + marketplace_bytes
        + b"\0"
        + block.encode("utf-8")
        + b"\0"
        + plugin_source_digest.encode("ascii")
        + b"\0"
        + str(plugin_install_path).encode("utf-8")
        + b"\0"
        + (b"absent" if cache_before is None else cache_before.encode("ascii"))
        + b"\0"
        + plugin_install_digest.encode("ascii")
        + b"\0"
        + str(binary.executable_path).encode("utf-8")
        + b"\0"
        + binary.executable_digest.encode("ascii")
        + b"\0"
        + binary.codex_version.encode("ascii")
        + b"\0"
        + canonical_encode(["--version"])
        + b"\0"
        + b"temporary_owner_private_home"
        + b"\0"
        + canonical_encode(
            {
                "CODEX_HOME": str(home),
                "CODEX_TESTING_HOME": str(home),
            }
        )
        + b"\0"
        + canonical_encode(list(_PLUGIN_LIST_COMMAND))
        + b"\0"
        + canonical_encode(list(_PLUGIN_ADD_COMMAND))
    )
    preview = ActivationPreview(
        marketplace_bytes,
        block,
        _sha(digest_body),
        inspection,
        plugin_source_digest,
        home,
        plugin_install_path,
        plugin_install_digest,
        binary.executable_path,
        binary.executable_digest,
        binary.codex_version,
        ("--version",),
        _PLUGIN_LIST_COMMAND,
        _PLUGIN_ADD_COMMAND,
        "temporary_owner_private_home",
        (("CODEX_HOME", str(home)), ("CODEX_TESTING_HOME", str(home))),
        _sha(b"" if marketplace_before is None else marketplace_before),
        _sha(config_bytes),
        cache_before is None,
    )
    config_after = _activated_config_bytes(config_bytes, config, project)
    return _ActivationPlan(
        preview,
        marketplace_before,
        config_before,
        config_after,
        cache_members,
        cache_before,
        binary,
    )


def preview_activation(
    target: IntegrationTarget,
    *,
    executable_path: str,
    codex_home: Path | str | None = None,
    _run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> ActivationPreview:
    """Return the exact proposed marketplace document, TOML block, and stale-safe digest."""

    return _activation_plan(
        target,
        executable_path=executable_path,
        codex_home=codex_home,
        _run=_run,
    ).preview


def _safe_parent(path: Path) -> None:
    ancestors: list[Path] = []
    current = path
    while not current.exists():
        ancestors.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    for directory in reversed(ancestors):
        try:
            directory.mkdir(mode=0o700)
        except OSError as exc:
            raise _error(IntegrationReason.WRITE_FAILED) from exc
    if path.is_symlink() or not path.is_dir():
        raise _error(IntegrationReason.TARGET_UNSAFE)


def _atomic_write(path: Path, payload: bytes) -> None:
    _safe_parent(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise _error(IntegrationReason.WRITE_FAILED) from exc


def _current_bytes(path: Path) -> bytes | None:
    if path.is_symlink():
        raise _error(IntegrationReason.TARGET_UNSAFE)
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error(IntegrationReason.TARGET_UNSAFE) from exc
    _owned_not_writable(path, directory=False)
    return payload


def _assert_snapshot(path: Path, expected: bytes | None) -> None:
    if _current_bytes(path) != expected:
        raise _error(IntegrationReason.PREVIEW_STALE)


class _ActivationLock:
    def __init__(self, target: IntegrationTarget, codex_home: Path | str | None) -> None:
        self._target = target
        self._codex_home = codex_home
        self._descriptor: int | None = None

    def __enter__(self) -> None:
        _validated_project(self._target)
        home = _codex_root(self._codex_home)
        _safe_parent(home)
        path = home / _ACTIVATION_LOCK
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            if descriptor is not None:
                os.close(descriptor)
            raise _error(IntegrationReason.TARGET_UNSAFE) from exc
        assert descriptor is not None
        self._descriptor = descriptor

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        descriptor = self._descriptor
        self._descriptor = None
        if descriptor is not None:
            os.close(descriptor)


def _assert_plugin_source(
    target: IntegrationTarget,
    expected_digest: str,
    expected_members: Mapping[str, bytes],
    *,
    codex_version: str,
) -> None:
    if _plugin_source_digest(codex_version=codex_version) != expected_digest:
        raise _error(IntegrationReason.PREVIEW_STALE)
    if (
        inspect_plugin(target, codex_version=codex_version).presence
        is not PluginHookPresence.INSTALLED
    ):
        raise _error(IntegrationReason.PARTIAL_INSTALL)
    if _source_cache_members(target, codex_version=codex_version) != dict(expected_members):
        raise _error(IntegrationReason.PREVIEW_STALE)


def _assert_binary_probe(
    expected: _CodexBinaryProbe,
    *,
    _run: Callable[..., subprocess.CompletedProcess[bytes]],
) -> None:
    current = _probe_codex_binary(
        str(expected.executable_path), codex_home=expected.codex_home, _run=_run
    )
    if current != expected:
        raise _error(IntegrationReason.PREVIEW_STALE)


def _validate_add_result(
    result: Mapping[str, object],
    preview: ActivationPreview,
) -> Path:
    if (
        result.get("pluginId") != _PLUGIN_ID
        or result.get("name") != "yoetz"
        or result.get("marketplaceName") != _MARKETPLACE_NAME
        or result.get("version") != __version__
    ):
        raise _error(IntegrationReason.WRITE_FAILED)
    installed_raw = result.get("installedPath")
    if type(installed_raw) is not str:
        raise _error(IntegrationReason.WRITE_FAILED)
    installed = Path(installed_raw)
    expected = preview.plugin_install_path
    if not installed.is_absolute() or installed != expected:
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    return installed


def _append_config_block(raw: bytes, block: str) -> bytes:
    if not block:
        return raw
    prefix = raw
    if prefix and not prefix.endswith(b"\n"):
        prefix += b"\n"
    if prefix:
        prefix += b"\n"
    return prefix + block.encode("utf-8")


def _activated_config_bytes(raw: bytes, config: Mapping[str, object], project: Path) -> bytes:
    configured, enabled, foreign = _config_state(config, project)
    if foreign:
        raise _error(IntegrationReason.DESTINATION_CONFLICT)
    if configured and enabled:
        return raw
    merged = _append_config_block(raw, _activation_block(project, config))
    try:
        tomllib.loads(merged.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        # Legal owner TOML such as inline ``marketplaces = {}`` makes the appended
        # table headers re-declarations; refuse at plan time instead of writing a
        # config Codex itself can no longer parse.
        raise _error(IntegrationReason.DESTINATION_CONFLICT) from exc
    return merged


def apply_activation(
    target: IntegrationTarget,
    *,
    approved_digest: str,
    executable_path: str,
    codex_home: Path | str | None = None,
    _run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> ActivationInspection:
    """Apply one exact approved preview, refusing stale or foreign state."""

    binary = _probe_codex_binary(executable_path, codex_home=codex_home, _run=_run)
    if codex_home is not None and _codex_root(codex_home) != binary.codex_home:
        raise _error(IntegrationReason.PREVIEW_STALE)
    with _ActivationLock(target, binary.codex_home):
        plan = _activation_plan(
            target,
            executable_path=executable_path,
            codex_home=codex_home,
            _run=_run,
        )
        preview = plan.preview
        if approved_digest != preview.preview_digest:
            raise _error(IntegrationReason.PREVIEW_STALE)
        _assert_plugin_source(
            target,
            preview.plugin_source_digest,
            plan.cache_members,
            codex_version=plan.binary.codex_version,
        )
        project, _home, marketplace_path, config_path = _paths(target, binary.codex_home)
        marketplace_changed = plan.marketplace_before != preview.marketplace_bytes
        config_changed = plan.config_before != plan.config_after
        if marketplace_changed:
            _assert_plugin_source(
                target,
                preview.plugin_source_digest,
                plan.cache_members,
                codex_version=plan.binary.codex_version,
            )
            _assert_snapshot(marketplace_path, plan.marketplace_before)
            _atomic_write(marketplace_path, preview.marketplace_bytes)
        try:
            if config_changed:
                _assert_plugin_source(
                    target,
                    preview.plugin_source_digest,
                    plan.cache_members,
                    codex_version=plan.binary.codex_version,
                )
                _assert_snapshot(config_path, plan.config_before)
                _atomic_write(config_path, plan.config_after)
            inventory_installed, inventory_version = _plugin_inventory(
                plan.binary, project, _run=_run
            )
            if inventory_installed:
                if inventory_version != __version__ or plan.cache_before is None:
                    raise _error(IntegrationReason.DESTINATION_CONFLICT)
            elif plan.cache_before is not None:
                raise _error(IntegrationReason.DESTINATION_CONFLICT)
            else:
                _assert_plugin_source(
                    target,
                    preview.plugin_source_digest,
                    plan.cache_members,
                    codex_version=plan.binary.codex_version,
                )
                _assert_binary_probe(plan.binary, _run=_run)
                add_result = _run_json_command(
                    plan.binary,
                    preview.install_command,
                    _run=_run,
                    timeout=_PLUGIN_ADD_TIMEOUT_SECONDS,
                )
                cache_created = _validate_add_result(add_result, preview)
                if (
                    _installed_cache_digest(cache_created, plan.cache_members)
                    != preview.plugin_install_digest
                ):
                    raise _error(IntegrationReason.WRITE_FAILED)
            _assert_snapshot(config_path, plan.config_after)
            _assert_snapshot(marketplace_path, preview.marketplace_bytes)
            _assert_plugin_source(
                target,
                preview.plugin_source_digest,
                plan.cache_members,
                codex_version=plan.binary.codex_version,
            )
        except IntegrationError:
            # Preserve already-approved partial state for an honest retry. Pathname
            # verify-then-rollback cannot exclude a non-cooperating concurrent replacement.
            raise
        inspection = inspect_activation(
            target,
            executable_path=executable_path,
            codex_home=binary.codex_home,
            _run=_run,
        )
        if inspection.state is not ActivationState.ACTIVE:
            raise _error(IntegrationReason.WRITE_FAILED)
    return inspection
