"""Connection-free proof of the runtime's resolved Yoetz identity roots (issue #518).

``yoetz service isolation`` resolves — locally, without touching any service, lock, or ledger —
which identity roots this exact process environment would use: state directory (service lock and
generation), runtime endpoint directory, effective storage bundle, selected config file, and the
Yoetz launcher. It reports each as a digest over the canonical resolved path identity, never as a
raw path. A dogfood preflight combines one exact normal-target report with one exact isolated
report, so relocated normal storage cannot be mistaken for separation.

A set but unusable ``YOETZ_ISOLATED_ROOT`` propagates as the bounded ``PathSafetyError`` — the
mode is then unprovable and callers must fail closed, never report ``ambient``.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Literal, TypedDict

from yoetz.config.installation import ReportedLifecycle, read_instance_identity
from yoetz.config.load import parse_minimal_safe_config
from yoetz.config.paths import (
    IsolationBinding,
    bundle_root,
    config_file_path,
    isolated_root,
    isolation_binding,
    runtime_dir,
    state_dir,
)

__all__ = ["IsolationReport", "isolation_report"]


class ResolvedIdentity(TypedDict):
    state_digest: str
    endpoint_digest: str
    storage_digest: str
    config_digest: str
    executable_digest: str


class IsolationReport(TypedDict):
    mode: Literal["isolated", "ambient"]
    binding: IsolationBinding
    lifecycle: ReportedLifecycle
    identity: ResolvedIdentity


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
    """Resolve only this exact environment's identity roots.

    Raises ``PathSafetyError`` when ``YOETZ_ISOLATED_ROOT`` or the runtime pin is set but
    unusable (or the two conflict), ``InstanceIdentityError`` when the root's instance marker is
    malformed, and ``ConfigError`` when the selected configuration cannot be minimally parsed;
    all mean the isolation state is unprovable and the caller must fail closed. A dogfood
    preflight compares this report with a second report captured from the exact normal target;
    platform defaults are not a substitute because that target may use relocated config or
    storage.
    """

    root = isolated_root()
    mode: Literal["isolated", "ambient"] = "ambient" if root is None else "isolated"
    binding = isolation_binding()
    # The everyday install is the permanent instance and carries no marker; an isolated root
    # without a marker is a legacy ADR-026 root and stays isolated, just unlabeled (issue #604).
    # A malformed marker propagates as ``InstanceIdentityError``: unprovable, never ambient.
    lifecycle: ReportedLifecycle = "permanent"
    if root is not None:
        marker = read_instance_identity(state_dir())
        lifecycle = "unlabeled" if marker is None else marker.lifecycle
    identity = ResolvedIdentity(
        state_digest=_identity_digest(state_dir()),
        endpoint_digest=_identity_digest(runtime_dir()),
        storage_digest=_identity_digest(_effective_storage_dir()),
        config_digest=_identity_digest(_selected_config_path()),
        # ``sys.argv[0]`` is the exact selected Yoetz launcher. ``sys.executable`` would identify
        # only the shared Python interpreter and could make two distinct installed targets look
        # identical (or the reverse when wrappers share one interpreter).
        executable_digest=_identity_digest(Path(sys.argv[0])),
    )
    return IsolationReport(mode=mode, binding=binding, lifecycle=lifecycle, identity=identity)
