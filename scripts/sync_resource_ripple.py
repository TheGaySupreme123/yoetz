"""Converge and verify every generated artifact in the packaged-resource ripple.

Resource inventory changes cross four generated layers: the package resource manifest, the
version-manifest schema, the schema inventory/runtime-support digests, and the packaged copies of
those files. This command owns that order and repeats it to a byte-identical fixed point instead
of requiring maintainers to remember a multi-pass sequence.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Final

_SCRIPT_ROOT = Path(__file__).resolve().parent
_DEFAULT_REPO_ROOT = _SCRIPT_ROOT.parent

_MAX_PASSES: Final = 5
_VERSION_MANIFEST_SCHEMA: Final = "version/version-manifest-1.0.0.schema.json"
_OWNED_ROOTS: Final = ("schemas", "src/yoetz/resources")
_OWNED_FILES: Final = (
    "skills/codex/yoetz/manifest.json",
    "support/runtime-support.json",
)


def _iter_owned_files(repo_root: Path) -> Iterator[Path]:
    """Yield every byte-owned artifact in deterministic path order."""

    paths: list[Path] = []
    for relative_root in _OWNED_ROOTS:
        root = repo_root / relative_root
        if root.is_dir():
            paths.extend(path for path in root.rglob("*") if path.is_file())
    paths.extend(repo_root / relative_path for relative_path in _OWNED_FILES)
    yield from sorted(set(paths), key=lambda path: path.relative_to(repo_root).as_posix().encode())


def _owned_digest(repo_root: Path) -> str:
    """Bind the complete owned byte set, including relative names, for convergence checks."""

    digest = hashlib.sha256()
    for path in _iter_owned_files(repo_root):
        relative = path.relative_to(repo_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _child_environment(repo_root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    source_root = str(repo_root / "src")
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source_root if not existing else os.pathsep.join((source_root, existing))
    )
    return environment


def _run(repo_root: Path, script_name: str, *arguments: str) -> bool:
    script = repo_root / "scripts" / script_name
    completed = subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=repo_root,
        env=_child_environment(repo_root),
        check=False,
    )
    return completed.returncode == 0


def _preflight(repo_root: Path) -> bool:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from verify_resource_manifest import load_inventory_config; "
            "from yoetz.version import REVIEWED_RESOURCE_COUNT; "
            "print(len(load_inventory_config().entries), REVIEWED_RESOURCE_COUNT)",
        ],
        cwd=repo_root / "scripts",
        env=_child_environment(repo_root),
        capture_output=True,
        check=False,
        text=True,
    )
    if probe.returncode != 0:
        print("sync_resource_ripple: FAIL (preflight_failed)", file=sys.stderr)
        if probe.stderr:
            print(probe.stderr.rstrip(), file=sys.stderr)
        return False
    try:
        actual_count, reviewed_count = (int(value) for value in probe.stdout.split())
    except ValueError:
        print("sync_resource_ripple: FAIL (preflight_output_invalid)", file=sys.stderr)
        return False
    if actual_count == reviewed_count:
        return True
    print(
        "sync_resource_ripple: FAIL (reviewed_resource_count_mismatch)\n"
        f"  inventory entries: {actual_count}\n"
        f"  REVIEWED_RESOURCE_COUNT: {reviewed_count}\n"
        "  review and update the single cardinality tripwire in src/yoetz/version.py first",
        file=sys.stderr,
    )
    return False


def _check(repo_root: Path) -> bool:
    return _run(repo_root, "generate_schemas.py", "--check") and _run(
        repo_root, "verify_resource_manifest.py", "--check"
    )


def _write_pass(repo_root: Path) -> bool:
    steps = (
        ("verify_resource_manifest.py", "--sync"),
        (
            "generate_schemas.py",
            "--write",
            "--only",
            _VERSION_MANIFEST_SCHEMA,
        ),
        ("sync_service_status_schema.py",),
        ("verify_resource_manifest.py", "--sync"),
    )
    for script_name, *arguments in steps:
        if not _run(repo_root, script_name, *arguments):
            print(f"sync_resource_ripple: FAIL (step_failed) {script_name}", file=sys.stderr)
            return False
    return True


def _write_to_fixed_point(repo_root: Path) -> bool:
    previous = _owned_digest(repo_root)
    for pass_number in range(1, _MAX_PASSES + 1):
        if not _write_pass(repo_root):
            return False
        current = _owned_digest(repo_root)
        if current == previous:
            if not _check(repo_root):
                print("sync_resource_ripple: FAIL (post_convergence_check)", file=sys.stderr)
                return False
            print(f"sync_resource_ripple: PASS (fixed point after {pass_number} pass(es))")
            return True
        previous = current
    print(
        f"sync_resource_ripple: FAIL (did_not_converge after {_MAX_PASSES} passes)",
        file=sys.stderr,
    )
    return False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_resource_ripple.py",
        description="Converge or verify generated schemas and packaged resource artifacts.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only fixed-point verification.")
    mode.add_argument(
        "--write", action="store_true", help="Regenerate to a byte-stable fixed point."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Test-only: operate on another complete checkout.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve() if args.repo_root is not None else _DEFAULT_REPO_ROOT
    if not repo_root.is_dir():
        print(f"sync_resource_ripple: FAIL (repo_root_missing) {repo_root}", file=sys.stderr)
        return 2
    if not _preflight(repo_root):
        return 1
    if args.check:
        if not _check(repo_root):
            print("sync_resource_ripple: FAIL (drift_detected)", file=sys.stderr)
            return 1
        print("sync_resource_ripple: PASS (generated artifacts are at a fixed point)")
        return 0
    return 0 if _write_to_fixed_point(repo_root) else 1


if __name__ == "__main__":
    sys.exit(main())
