"""The packaged-resource owning command converges, checks, and fails before partial writes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Final, cast

import pytest

from yoetz.protocol.canonical import JsonValue, canonical_encode

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RIPPLE_SCRIPT = _REPO_ROOT / "scripts" / "sync_resource_ripple.py"

# Every tree the inventory reads from or writes into, so a copied checkout can run the real ripple.
_CHECKOUT_TREES: Final = (
    "fixtures/agent-plugins",
    "fixtures/canonical",
    "guidance",
    "migrations",
    "schemas",
    "scripts",
    "skills",
    "src/yoetz",
    "support",
)


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _copy_checkout(destination: Path) -> None:
    """Copy the working-tree source and generated trees the ripple reads and writes."""

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for relative in _CHECKOUT_TREES:
        shutil.copytree(_REPO_ROOT / relative, destination / relative, ignore=ignore)


def _synthetic_checkout(root: Path, *, inventory_count: int, reviewed_count: int) -> None:
    _write(root, "src/yoetz/__init__.py", "")
    _write(
        root,
        "src/yoetz/version.py",
        f"REVIEWED_RESOURCE_COUNT = {reviewed_count}\n"
        "\n"
        "def build_version_manifest():\n"
        "    return {}\n"
        "\n"
        "def version_manifest_json(manifest, *, include_resources=False):\n"
        "    return b'{}'\n",
    )
    _write(root, "schemas/version/version-manifest-2.0.0.schema.json", '{"type":"object"}')
    _write(
        root,
        "scripts/verify_resource_manifest.py",
        "class _Inventory:\n"
        f"    entries = tuple(range({inventory_count}))\n"
        "\n"
        "def load_inventory_config():\n"
        "    return _Inventory()\n",
    )
    _write(
        root,
        "scripts/generate_schemas.py",
        "from pathlib import Path\n"
        "import sys\n"
        "root = Path(__file__).resolve().parent.parent\n"
        "state = root / 'schemas/state.txt'\n"
        "if '--write' in sys.argv:\n"
        "    state.write_text('stable\\n', encoding='utf-8')\n"
        "elif state.read_text(encoding='utf-8') != 'stable\\n':\n"
        "    raise SystemExit(1)\n",
    )
    _write(root, "scripts/sync_service_status_schema.py", "")
    _write(root, "schemas/state.txt", "stale\n")
    _write(root, "src/yoetz/resources/manifest.json", "{}\n")
    _write(root, "skills/codex/yoetz/manifest.json", "{}\n")
    _write(root, "support/runtime-support.json", "{}\n")


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_RIPPLE_SCRIPT), *arguments],
        cwd=_REPO_ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=600,
    )


def test_real_checkout_passes_the_single_ci_entrypoint() -> None:
    completed = _run("--check")

    assert completed.returncode == 0, completed.stderr
    assert "generated artifacts are at a fixed point" in completed.stdout


def test_write_repeats_until_the_owned_bytes_are_stable(tmp_path: Path) -> None:
    _synthetic_checkout(tmp_path, inventory_count=1, reviewed_count=1)

    completed = _run("--write", "--repo-root", str(tmp_path))

    assert completed.returncode == 0, completed.stderr
    assert "fixed point after 2 pass(es)" in completed.stdout
    assert (tmp_path / "schemas/state.txt").read_text(encoding="utf-8") == "stable\n"


@pytest.mark.slow
def test_write_converges_a_reviewed_source_byte_change_in_a_real_checkout(tmp_path: Path) -> None:
    """A guidance byte change ripples through every dependent digest in one command."""

    checkout = tmp_path / "checkout"
    _copy_checkout(checkout)
    guidance = checkout / "guidance/workflow.md"
    guidance.write_bytes(guidance.read_bytes() + b"\n<!-- ripple probe -->\n")

    written = _run("--write", "--repo-root", str(checkout))
    assert written.returncode == 0, written.stderr + written.stdout

    assert (checkout / "src/yoetz/resources/guidance/workflow.md").read_bytes() == (
        guidance.read_bytes()
    )
    support = json.loads((checkout / "support/runtime-support.json").read_bytes())
    package_manifest = json.loads((checkout / "src/yoetz/resources/manifest.json").read_bytes())
    assert support["resource_set_digest"] == package_manifest["resource_set_digest"]

    checked = _run("--check", "--repo-root", str(checkout))
    assert checked.returncode == 0, checked.stderr + checked.stdout


@pytest.mark.slow
def test_check_rejects_a_self_consistent_but_stale_cardinality_constant(tmp_path: Path) -> None:
    """Byte-parity alone cannot see a wrong generated cardinality; the owning check must."""

    checkout = tmp_path / "checkout"
    _copy_checkout(checkout)
    schema_path = checkout / "schemas/version/version-manifest-2.0.0.schema.json"
    document = cast(dict[str, Any], json.loads(schema_path.read_bytes()))
    counts = document["$defs"]["resource_counts"]["properties"]
    counts["migrations"]["const"] = str(int(counts["migrations"]["const"]) - 1)
    counts["schema_resources"]["const"] = str(int(counts["schema_resources"]["const"]) + 1)
    schema_path.write_bytes(canonical_encode(cast(JsonValue, document)))

    # Re-mirror so every byte-parity comparison in the ripple is satisfied by the stale artifact.
    mirrored = subprocess.run(
        [sys.executable, str(checkout / "scripts/verify_resource_manifest.py"), "--sync"],
        cwd=checkout,
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, "PYTHONPATH": str(checkout / "src")},
        timeout=120,
    )
    assert mirrored.returncode == 0, mirrored.stderr

    checked = _run("--check", "--repo-root", str(checkout))

    assert checked.returncode == 1
    assert "installed_manifest_disagrees_with_schema" in checked.stderr


def test_reviewed_count_mismatch_fails_before_any_generator_runs(tmp_path: Path) -> None:
    _synthetic_checkout(tmp_path, inventory_count=2, reviewed_count=1)
    sentinel = tmp_path / "schemas/state.txt"

    completed = _run("--write", "--repo-root", str(tmp_path))

    assert completed.returncode == 1
    assert "reviewed_resource_count_mismatch" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "stale\n"
