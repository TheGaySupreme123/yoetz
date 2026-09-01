"""Connection-free proof of the runtime's resolved Yoetz identity roots (issue #518).

``yoetz service isolation`` resolves — locally, without touching any service, lock, or ledger —
which identity roots this exact process environment would use: state directory (service lock and
generation), runtime endpoint directory, effective storage bundle, selected config file, and the
runtime executable. It reports each as a digest over the canonical resolved path identity, never
as a raw path, next to the ambient platform-default identities, so a dogfood preflight can PROVE
that an isolated runtime shares nothing with the normal Yoetz target instead of assuming it.

A set but unusable ``YOETZ_ISOLATED_ROOT`` propagates as the bounded ``PathSafetyError`` — the
mode is then unprovable and callers must fail closed, never report ``ambient``.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Final, Literal, TypedDict

from platformdirs import PlatformDirs

from yoetz.config.load import parse_minimal_safe_config
from yoetz.config.paths import (
    bundle_root,
    config_file_path,
    isolated_root,
    runtime_dir,
    state_dir,
)

__all__ = ["IsolationReport", "isolation_report"]

_APP_NAME: Final = "yoetz"


class ResolvedIdentity(TypedDict):
    state_digest: str
    endpoint_digest: str
    storage_digest: str
    config_digest: str
    executable_digest: str


class AmbientIdentity(TypedDict):
    state_digest: str
    endpoint_digest: str
    storage_digest: str
    config_digest: str


class IsolationReport(TypedDict):
    mode: Literal["isolated", "ambient"]
    distinct: bool
    identity: ResolvedIdentity
    ambient_identity: AmbientIdentity


def _identity_digest(path: Path) -> str:
    """Digest over the canonical resolved path identity; never publishes the path itself."""

    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path
    return "sha256:" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()


def _selected_config_path() -> Path:
    explicit = os.environ.get("YOETZ_CONFIG", "")
    return Path(explicit) if explicit else config_file_path()


def _effective_storage_dir() -> Path:
    """The storage bundle the runtime would actually open, honoring config and env overrides."""

    minimal = parse_minimal_safe_config(os.environ, {})
    if minimal.data_dir is not None:
        return minimal.data_dir
    return bundle_root()


def isolation_report() -> IsolationReport:
    """Resolve the exact-environment identity roots and the ambient-default counterparts.

    Raises ``PathSafetyError`` when ``YOETZ_ISOLATED_ROOT`` is set but unusable and
    ``ConfigError`` when the selected configuration cannot be minimally parsed; both mean the
    isolation state is unprovable and the caller must fail closed.
    """

    ambient_dirs = PlatformDirs(appname=_APP_NAME, appauthor=False, roaming=False)
    ambient = AmbientIdentity(
        state_digest=_identity_digest(Path(ambient_dirs.user_state_dir)),
        endpoint_digest=_identity_digest(Path(ambient_dirs.user_runtime_path)),
        # Ambient storage/config are the normal target's DEFAULT identities. The normal
        # install's config file is deliberately not read here: an isolated runtime must not
        # touch the ambient install even read-only, so a normal target relocated by its own
        # config is compared against its platform default identity instead.
        storage_digest=_identity_digest(Path(ambient_dirs.user_data_dir)),
        config_digest=_identity_digest(Path(ambient_dirs.user_config_dir) / "config.toml"),
    )
    root = isolated_root()
    mode: Literal["isolated", "ambient"] = "ambient" if root is None else "isolated"
    identity = ResolvedIdentity(
        state_digest=_identity_digest(state_dir()),
        endpoint_digest=_identity_digest(runtime_dir()),
        storage_digest=_identity_digest(_effective_storage_dir()),
        config_digest=_identity_digest(_selected_config_path()),
        executable_digest=_identity_digest(Path(sys.executable)),
    )
    pairs = (
        (identity["state_digest"], ambient["state_digest"]),
        (identity["endpoint_digest"], ambient["endpoint_digest"]),
        (identity["storage_digest"], ambient["storage_digest"]),
        (identity["config_digest"], ambient["config_digest"]),
    )
    distinct = mode == "isolated" and all(resolved != normal for resolved, normal in pairs)
    return IsolationReport(
        mode=mode,
        distinct=distinct,
        identity=identity,
        ambient_identity=ambient,
    )
