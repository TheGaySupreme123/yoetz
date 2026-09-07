"""The packaged-resource owning command converges, checks, and fails before partial writes."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Final, cast

import pytest

from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RIPPLE_SCRIPT = _REPO_ROOT / "scripts" / "sync_resource_ripple.py"

# Every tree the inventory reads from or writes into, so a copied checkout can run the real ripple.
_CHECKOUT_TREES: Final = (
    ".agents/plugins/yoetz",
    ".agents/skills/yoetz",
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
    _write(root, "schemas/version/version-manifest-2.2.0.schema.json", '{"type":"object"}')
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
    _write(root, "scripts/sync_repository_authority_schemas.py", "")
    _write(root, "scripts/sync_committed_agent_trees.py", "")
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
    marker = checkout / ".agents/skills/yoetz/.yoetz-install.json"
    previous_marker = marker.read_bytes()

    written = _run("--write", "--repo-root", str(checkout))
    assert written.returncode == 0, written.stderr + written.stdout

    assert (checkout / "src/yoetz/resources/guidance/workflow.md").read_bytes() == (
        guidance.read_bytes()
    )
    support = json.loads((checkout / "support/runtime-support.json").read_bytes())
    package_manifest = json.loads((checkout / "src/yoetz/resources/manifest.json").read_bytes())
    assert support["resource_set_digest"] == package_manifest["resource_set_digest"]
    for relative in (
        ".agents/plugins/yoetz/skills/yoetz/references/workflow.md",
        ".agents/skills/yoetz/references/workflow.md",
    ):
        assert (checkout / relative).read_bytes() == guidance.read_bytes()
    assert marker.read_bytes() != previous_marker

    checked = _run("--check", "--repo-root", str(checkout))
    assert checked.returncode == 0, checked.stderr + checked.stdout

    before_second_write = {
        path.relative_to(checkout).as_posix(): path.read_bytes()
        for root in (".agents", "src/yoetz/resources", "schemas", "skills", "support")
        for path in (checkout / root).rglob("*")
        if path.is_file()
    }
    repeated = _run("--write", "--repo-root", str(checkout))
    assert repeated.returncode == 0, repeated.stderr + repeated.stdout
    assert before_second_write == {
        path.relative_to(checkout).as_posix(): path.read_bytes()
        for root in (".agents", "src/yoetz/resources", "schemas", "skills", "support")
        for path in (checkout / root).rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize(
    "relative",
    [".agents/plugins/yoetz/skills/yoetz/SKILL.md", ".agents/skills/yoetz/.yoetz-install.json"],
)
def test_agent_only_drift_is_checked_and_repaired_by_the_owning_command(
    tmp_path: Path, relative: str
) -> None:
    checkout = tmp_path / "checkout"
    _copy_checkout(checkout)
    target = checkout / relative
    original = target.read_bytes()
    target.write_bytes(b"stale generated bytes\n")

    checked = _run("--check", "--repo-root", str(checkout))
    assert checked.returncode == 1
    assert "agent_tree_drift" in checked.stderr
    assert "canonical owners: guidance/, skills/codex/yoetz/" in checked.stderr
    assert "uv run python scripts/sync_resource_ripple.py --write" in checked.stderr
    assert target.read_bytes() == b"stale generated bytes\n"

    written = _run("--write", "--repo-root", str(checkout))
    assert written.returncode == 0, written.stderr + written.stdout
    assert target.read_bytes() == original


def test_linked_agent_parent_fails_before_resource_writes(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    _copy_checkout(checkout)
    outside = tmp_path / "foreign-install"
    shutil.move(checkout / ".agents/skills", outside)
    (checkout / ".agents/skills").symlink_to(outside, target_is_directory=True)
    marker = (outside / "yoetz/.yoetz-install.json").read_bytes()
    packaged = checkout / "src/yoetz/resources/guidance/workflow.md"
    original = packaged.read_bytes()
    (checkout / "guidance/workflow.md").write_bytes(original + b"\n<!-- change -->\n")

    written = _run("--write", "--repo-root", str(checkout))
    assert written.returncode == 1
    assert "unsafe_agent_tree" in written.stderr
    assert packaged.read_bytes() == original
    assert (outside / "yoetz/.yoetz-install.json").read_bytes() == marker


def test_foreign_agent_file_is_preserved(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    _copy_checkout(checkout)
    foreign = checkout / ".agents/skills/yoetz/foreign.txt"
    foreign.write_bytes(b"keep this file\n")
    written = _run("--write", "--repo-root", str(checkout))
    assert written.returncode == 1
    assert "foreign_agent_files" in written.stderr
    assert foreign.read_bytes() == b"keep this file\n"


def test_obsolete_generated_member_requires_its_old_marker_binding(tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    _copy_checkout(checkout)
    root = checkout / ".agents/skills/yoetz"
    relative = "references/obsolete.md"
    data = b"obsolete generated reference\n"
    (root / relative).write_bytes(data)
    marker_path = root / ".yoetz-install.json"
    marker = json.loads(marker_path.read_bytes())
    marker.pop("marker_digest")
    marker["managed_files"].append(
        {
            "relative_path": relative,
            "size": len(data),
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
        }
    )
    marker["marker_digest"] = canonical_digest(cast(JsonValue, marker))
    marker_path.write_bytes(canonical_encode(cast(JsonValue, marker)) + b"\n")

    written = _run("--write", "--repo-root", str(checkout))
    assert written.returncode == 0, written.stderr + written.stdout
    assert not (root / relative).exists()


@pytest.mark.slow
def test_check_rejects_a_self_consistent_but_stale_cardinality_constant(tmp_path: Path) -> None:
    """Byte-parity alone cannot see a wrong generated cardinality; the owning check must."""

    checkout = tmp_path / "checkout"
    _copy_checkout(checkout)
    schema_path = checkout / "schemas/version/version-manifest-2.2.0.schema.json"
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
