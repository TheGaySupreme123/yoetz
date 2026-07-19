"""Platform-native paths and the private-local bundle safety gate."""

from __future__ import annotations

import ctypes
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from platformdirs import PlatformDirs

__all__ = [
    "PathSafetyError",
    "bundle_root",
    "cache_dir",
    "catalog_path",
    "config_file_path",
    "ensure_owner_only_dir",
    "log_dir",
    "service_generation_path",
    "state_dir",
    "task_bundle_dir",
    "unlock_throttle_path",
    "verify_private_local_bundle",
]

_APP_NAME: Final = "yoetz"
_PRIVATE_DIR_MODE: Final = 0o700
_VCS_MARKERS: Final = frozenset({".git", ".hg", ".svn", ".jj"})
_NETWORK_FILESYSTEMS_LINUX: Final = frozenset(
    {
        "9p",
        "afs",
        "ceph",
        "cifs",
        "davfs",
        "fuse.rclone",
        "fuse.sshfs",
        "glusterfs",
        "nfs",
        "nfs4",
        "smb3",
        "smbfs",
        "sshfs",
    }
)
_NETWORK_FILESYSTEMS_MACOS: Final = frozenset({"acfs", "afpfs", "nfs", "smbfs", "webdav"})
_LINUX_SYNC_COMPONENTS: Final = frozenset(
    {"Google Drive", "GoogleDrive", "Insync", "Nextcloud", "OneDrive", "Sync", "ownCloud"}
)
_SYNC_METADATA_NAMES: Final = frozenset({".dropbox", ".dropbox.cache", ".stfolder", ".sync"})
_PATH_SAFETY_REASONS: Final = frozenset(
    {
        "path_contains_symlink",
        "path_in_repository",
        "path_in_sync_folder",
        "path_not_owned",
        "path_on_network_filesystem",
        "path_shared_temp",
        "permissions_too_broad",
    }
)


class PathSafetyError(Exception):
    """A bounded path-safety failure suitable for STORAGE_UNSAFE mapping."""

    reason_code: str

    def __init__(self, reason_code: str) -> None:
        if type(reason_code) is not str or reason_code not in _PATH_SAFETY_REASONS:
            raise ValueError("path_safety_reason_invalid")
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class _PathProbe:
    """Private injection seam for deterministic path-classifier tests."""

    platform: str
    effective_uid: int
    home: Path
    shared_temp: Path
    fixed_shared_temps: tuple[Path, ...]
    platform_dirs: PlatformDirs
    mount_table: Callable[[], str]
    macos_fstype: Callable[[Path], str | None]
    diagnostic: Callable[[str], None]


def _ignore_diagnostic(_reason_code: str) -> None:
    return None


def _read_mount_table() -> str:
    return Path("/proc/mounts").read_text(encoding="utf-8")


class _DarwinStatFs(ctypes.Structure):
    # Darwin's public struct statfs layout. This branch is never selected on Linux.
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_reserved", ctypes.c_uint32 * 8),
    ]


def _darwin_fstype(path: Path) -> str | None:
    if sys.platform != "darwin":
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    statfs = libc.statfs
    statfs.argtypes = [ctypes.c_char_p, ctypes.POINTER(_DarwinStatFs)]
    statfs.restype = ctypes.c_int
    result = _DarwinStatFs()
    if statfs(os.fsencode(path), ctypes.byref(result)) != 0:
        return None
    return bytes(result.f_fstypename).split(b"\0", 1)[0].decode("ascii", errors="strict")


def _default_probe() -> _PathProbe:
    dirs = PlatformDirs(appname=_APP_NAME, appauthor=False, roaming=False)
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else os.getuid()
    return _PathProbe(
        platform=sys.platform,
        effective_uid=effective_uid,
        home=Path.home(),
        shared_temp=Path(tempfile.gettempdir()),
        fixed_shared_temps=(Path("/tmp"), Path("/var/tmp"), Path("/dev/shm")),
        platform_dirs=dirs,
        mount_table=_read_mount_table,
        macos_fstype=_darwin_fstype,
        diagnostic=_ignore_diagnostic,
    )


def _probe_or_default(probe: _PathProbe | None) -> _PathProbe:
    return _default_probe() if probe is None else probe


def bundle_root(*, _data_dir: Path | None = None, _probe: _PathProbe | None = None) -> Path:
    """Return the default bundle root or a safety-verified explicit override."""

    probe = _probe_or_default(_probe)
    if _data_dir is None:
        return Path(probe.platform_dirs.user_data_dir)
    verify_private_local_bundle(_data_dir, _probe=probe)
    return _data_dir


def catalog_path(*, _data_dir: Path | None = None, _probe: _PathProbe | None = None) -> Path:
    """Return the catalog database path inside the selected bundle root."""

    return bundle_root(_data_dir=_data_dir, _probe=_probe) / "catalog.sqlite3"


def task_bundle_dir(
    task_id: str, *, _data_dir: Path | None = None, _probe: _PathProbe | None = None
) -> Path:
    """Return the task bundle directory for an already validated task ID."""

    return bundle_root(_data_dir=_data_dir, _probe=_probe) / "tasks" / task_id


def config_file_path(*, _probe: _PathProbe | None = None) -> Path:
    """Return the sole default user configuration file path."""

    probe = _probe_or_default(_probe)
    return Path(probe.platform_dirs.user_config_dir) / "config.toml"


def cache_dir(*, _probe: _PathProbe | None = None) -> Path:
    """Return the platform-native user cache directory."""

    return Path(_probe_or_default(_probe).platform_dirs.user_cache_dir)


def state_dir(*, _probe: _PathProbe | None = None) -> Path:
    """Return the platform-native user state directory."""

    return Path(_probe_or_default(_probe).platform_dirs.user_state_dir)


def log_dir(*, _probe: _PathProbe | None = None) -> Path:
    """Return the platform-native user log directory."""

    return Path(_probe_or_default(_probe).platform_dirs.user_log_dir)


def service_generation_path(*, _probe: _PathProbe | None = None) -> Path:
    """Return the fixed locked-state service-generation metadata path."""

    root = state_dir(_probe=_probe)
    verify_private_local_bundle(root, _probe=_probe)
    ensure_owner_only_dir(root)
    verify_private_local_bundle(root, _probe=_probe)
    return root / "service-generation.json"


def unlock_throttle_path(*, _probe: _PathProbe | None = None) -> Path:
    """Return the fixed passphrase-throttle metadata path."""

    root = state_dir(_probe=_probe)
    verify_private_local_bundle(root, _probe=_probe)
    ensure_owner_only_dir(root)
    verify_private_local_bundle(root, _probe=_probe)
    return root / "unlock-throttle.json"


def _absolute_without_resolving(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            return candidate
        candidate = parent
    return candidate


def _ancestors_inclusive(path: Path) -> tuple[Path, ...]:
    return (path, *path.parents)


def _is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _reject_symlink_components(path: Path) -> None:
    absolute = _absolute_without_resolving(path)
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PathSafetyError("path_contains_symlink") from exc
        if stat.S_ISLNK(mode):
            raise PathSafetyError("path_contains_symlink")


def _check_owner_and_mode(path: Path, probe: _PathProbe) -> None:
    if not path.exists():
        return
    try:
        facts = path.stat()
    except OSError:
        probe.diagnostic("path_posix_facts_unavailable")
        return
    if hasattr(facts, "st_uid") and facts.st_uid != probe.effective_uid:
        raise PathSafetyError("path_not_owned")
    try:
        mode = stat.S_IMODE(facts.st_mode)
    except AttributeError, TypeError, ValueError:
        probe.diagnostic("path_mode_unavailable")
        return
    if mode & 0o077:
        raise PathSafetyError("permissions_too_broad")


def _check_shared_temp(path: Path, probe: _PathProbe) -> None:
    for root in probe.fixed_shared_temps:
        try:
            resolved_root = root.resolve(strict=False)
        except OSError:
            resolved_root = root
        if _is_beneath(path, resolved_root):
            raise PathSafetyError("path_shared_temp")

    try:
        shared_temp = probe.shared_temp.resolve(strict=False)
        temp_mode = stat.S_IMODE(shared_temp.stat().st_mode)
    except OSError:
        probe.diagnostic("shared_temp_probe_failed")
        return
    if temp_mode & 0o077 and _is_beneath(path, shared_temp):
        raise PathSafetyError("path_shared_temp")


def _check_repository(path: Path) -> None:
    for ancestor in _ancestors_inclusive(path):
        if any((ancestor / marker).exists() for marker in _VCS_MARKERS):
            raise PathSafetyError("path_in_repository")


def _contains_component(path: Path, name: str) -> bool:
    return name in path.parts


def _check_sync_folder(path: Path, probe: _PathProbe) -> None:
    ancestors = _ancestors_inclusive(path)
    if _contains_component(path, "Dropbox"):
        raise PathSafetyError("path_in_sync_folder")
    if any(
        (ancestor / marker).exists() for ancestor in ancestors for marker in _SYNC_METADATA_NAMES
    ):
        raise PathSafetyError("path_in_sync_folder")

    if probe.platform == "darwin":
        mobile_documents = probe.home / "Library" / "Mobile Documents"
        cloud_storage = probe.home / "Library" / "CloudStorage"
        if _is_beneath(path, mobile_documents):
            raise PathSafetyError("path_in_sync_folder")
        if any(ancestor.name.endswith(".icloud") for ancestor in ancestors):
            raise PathSafetyError("path_in_sync_folder")
        if _is_beneath(path, cloud_storage) and path != cloud_storage:
            raise PathSafetyError("path_in_sync_folder")

    if probe.platform.startswith("linux"):
        for index, component in enumerate(path.parts):
            if component not in _LINUX_SYNC_COMPONENTS:
                continue
            component_path = Path(*path.parts[: index + 1])
            parent = component_path.parent
            if any((parent / marker).exists() for marker in _SYNC_METADATA_NAMES):
                raise PathSafetyError("path_in_sync_folder")


def _decode_mount_field(value: str) -> str:
    result = value
    for escaped, literal in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
        result = result.replace(escaped, literal)
    return result


def _linux_mount_fstype(path: Path, mount_table: str) -> str | None:
    matches: list[tuple[int, str]] = []
    for line in mount_table.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount_point = Path(_decode_mount_field(fields[1]))
        try:
            resolved_mount = mount_point.resolve(strict=False)
        except OSError:
            resolved_mount = mount_point
        if _is_beneath(path, resolved_mount):
            matches.append((len(resolved_mount.parts), fields[2]))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _check_network_filesystem(path: Path, probe: _PathProbe) -> None:
    stat_target = _existing_ancestor(path)
    if probe.platform.startswith("linux"):
        try:
            os.statvfs(stat_target)
            mount_table = probe.mount_table()
            filesystem = _linux_mount_fstype(path, mount_table)
        except Exception:
            probe.diagnostic("network_filesystem_probe_failed")
            return
        if filesystem in _NETWORK_FILESYSTEMS_LINUX:
            raise PathSafetyError("path_on_network_filesystem")
        return

    if probe.platform == "darwin":
        try:
            filesystem = probe.macos_fstype(stat_target)
        except Exception:
            probe.diagnostic("network_filesystem_probe_failed")
            return
        if filesystem is None:
            probe.diagnostic("network_filesystem_probe_failed")
        elif filesystem in _NETWORK_FILESYSTEMS_MACOS:
            raise PathSafetyError("path_on_network_filesystem")


def _record_location_diagnostic(path: Path, probe: _PathProbe) -> None:
    approved_roots = (
        Path(probe.platform_dirs.user_data_dir),
        Path(probe.platform_dirs.user_config_dir),
        Path(probe.platform_dirs.user_cache_dir),
        Path(probe.platform_dirs.user_state_dir),
        Path(probe.platform_dirs.user_log_dir),
    )
    try:
        approved = tuple(root.resolve(strict=False) for root in approved_roots)
    except OSError:
        probe.diagnostic("approved_path_probe_failed")
        return
    if not _is_beneath(path, probe.home) and not any(_is_beneath(path, root) for root in approved):
        probe.diagnostic("path_outside_user_home")


def verify_private_local_bundle(path: Path, *, _probe: _PathProbe | None = None) -> None:
    """Verify a bundle path without creating or modifying it.

    Checks execute in the contractually fixed order and stop at the first
    reason-coded unsafe condition.
    """

    probe = _probe_or_default(_probe)
    _reject_symlink_components(path)
    try:
        resolved = path.resolve(strict=False)
    except OSError as exc:
        raise PathSafetyError("path_contains_symlink") from exc
    _check_owner_and_mode(resolved, probe)
    _check_shared_temp(resolved, probe)
    _check_repository(resolved)
    _check_sync_folder(resolved, probe)
    _check_network_filesystem(resolved, probe)
    _record_location_diagnostic(resolved, probe)


def ensure_owner_only_dir(path: Path) -> None:
    """Create an owner-only directory if absent, then verify owner and mode."""

    _reject_symlink_components(path)
    missing: list[Path] = []
    candidate = path
    while not candidate.exists():
        missing.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    try:
        for directory in reversed(missing):
            directory.mkdir(mode=_PRIVATE_DIR_MODE)
            created = directory.lstat()
            if stat.S_IMODE(created.st_mode) != _PRIVATE_DIR_MODE:
                raise PathSafetyError("permissions_too_broad")
        facts = path.lstat()
    except PathSafetyError:
        raise
    except OSError as exc:
        raise PathSafetyError("permissions_too_broad") from exc
    if stat.S_ISLNK(facts.st_mode) or not stat.S_ISDIR(facts.st_mode):
        raise PathSafetyError("path_contains_symlink")
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else os.getuid()
    if hasattr(facts, "st_uid") and facts.st_uid != effective_uid:
        raise PathSafetyError("path_not_owned")
    if stat.S_IMODE(facts.st_mode) != _PRIVATE_DIR_MODE:
        raise PathSafetyError("permissions_too_broad")
