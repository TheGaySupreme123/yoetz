"""The packaged-resource owning command converges, checks, and fails before partial writes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RIPPLE_SCRIPT = _REPO_ROOT / "scripts" / "sync_resource_ripple.py"


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _synthetic_checkout(root: Path, *, inventory_count: int, reviewed_count: int) -> None:
    _write(root, "src/yoetz/__init__.py", "")
    _write(
        root,
        "src/yoetz/version.py",
        f"REVIEWED_RESOURCE_COUNT = {reviewed_count}\n",
    )
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
        timeout=120,
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


def test_reviewed_count_mismatch_fails_before_any_generator_runs(tmp_path: Path) -> None:
    _synthetic_checkout(tmp_path, inventory_count=2, reviewed_count=1)
    sentinel = tmp_path / "schemas/state.txt"

    completed = _run("--write", "--repo-root", str(tmp_path))

    assert completed.returncode == 1
    assert "reviewed_resource_count_mismatch" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "stale\n"
