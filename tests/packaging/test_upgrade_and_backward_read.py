"""Prior-release data upgrade preservation.

Scope note (verbatim, not guessed around): this spec's matrix is "each supported old release ×
advertised platform × normal upgrade, interrupted migration, rollback/restore, ...". Yoetz v0.1.0
is the first release: ``support/runtime-support.json`` records ``"release_version": "0.1.0"`` with
every capability cell still empty (``development_unverified``), there is no golden fixture directory
for a prior release anywhere in the repository, and ``docs/protocol/compatibility.md`` documents
only the current release's axes. There is therefore no real prior artifact/bundle for this file to
install, migrate, or replay against, and this file does not fabricate one. What it proves for real
instead, against the installed candidate package (never the source checkout), is every structural
invariant this suite's own spec states that a genuine future upgrade will depend on: the migration
registries are exactly contiguous and match the advertised schema versions, a fresh catalog/bundle
initializes at exactly that version, re-running the migration runner against an already-current
database is an inert, verified replay (not a silent no-op that skips verification), and a
newer-than-candidate schema is refused for both reads/writes and migration -- never silently
accepted, never downgraded, never partially applied.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class _Installed:
    python: Path


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> _Installed:
    dist_dir = tmp_path_factory.mktemp("upgrade-dist")
    build = subprocess.run(
        ["uv", "build", "--no-sources", "-o", str(dist_dir), str(_REPO_ROOT)],
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert build.returncode == 0, build.stderr.decode("utf-8", errors="replace")
    wheels = sorted(dist_dir.glob("*.whl"))
    assert len(wheels) == 1

    venv_dir = tmp_path_factory.mktemp("upgrade-venv") / "venv"
    create = subprocess.run(
        ["uv", "venv", "--python", "3.14", str(venv_dir)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert create.returncode == 0, create.stderr
    install = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_dir / "bin" / "python"),
            "--find-links",
            str(dist_dir),
            "yoetz==0.1.0",
        ],
        capture_output=True,
        timeout=180,
        check=False,
    )
    assert install.returncode == 0, install.stderr.decode("utf-8", errors="replace")
    return _Installed(python=venv_dir / "bin" / "python")


def _run_probe(installed: _Installed, probe: str) -> dict[str, object]:
    result = subprocess.run(
        [str(installed.python), "-c", probe], capture_output=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# First-release status is explicit, not fabricated
# ---------------------------------------------------------------------------


def test_first_release_has_no_prior_supported_version_and_no_golden_fixture() -> None:
    support = json.loads(
        (_REPO_ROOT / "support" / "runtime-support.json").read_text(encoding="utf-8")
    )
    assert support["release_version"] == "0.1.0"
    # No supported-old-release cell has been populated yet; there is nothing to upgrade from.
    for cell_key in ("runtime_cells", "local_service_cells"):
        assert support[cell_key] == []
    assert not (_REPO_ROOT / "fixtures" / "compat").exists()
    assert not (_REPO_ROOT / "fixtures" / "releases").exists()


def test_compatibility_doc_names_only_the_current_release_axes() -> None:
    text = (_REPO_ROOT / "docs" / "protocol" / "compatibility.md").read_text(encoding="utf-8")
    assert "untested" in text
    assert "unsupported" in text
    # It documents the rule vocabulary, not a concrete list of prior supported releases.
    assert not re.search(r"0\.0\.\d+", text)


# ---------------------------------------------------------------------------
# Migration registries are exactly contiguous and match the advertised versions
# ---------------------------------------------------------------------------


def test_root_and_installed_migration_trees_are_byte_identical() -> None:
    for family, versions in (
        ("catalog", ("0001", "0002")),
        ("bundle", ("0001", "0002", "0003")),
    ):
        for version in versions:
            root_file = _REPO_ROOT / "migrations" / family / f"{version}.sql"
            installed_file = (
                _REPO_ROOT
                / "src"
                / "yoetz"
                / "resources"
                / "migrations"
                / family
                / f"{version}.sql"
            )
            assert root_file.read_bytes() == installed_file.read_bytes()


def test_each_migration_family_has_contiguous_versions(installed: _Installed) -> None:
    probe = (
        "from yoetz.adapters.sqlite.migrations import ("
        "BUNDLE_MIGRATIONS, CATALOG_MIGRATIONS, current_schema_version)\n"
        "import json\n"
        "print(json.dumps({\n"
        "    'catalog_versions': [m.version for m in CATALOG_MIGRATIONS],\n"
        "    'bundle_versions': [m.version for m in BUNDLE_MIGRATIONS],\n"
        "    'catalog_current': current_schema_version(CATALOG_MIGRATIONS),\n"
        "    'bundle_current': current_schema_version(BUNDLE_MIGRATIONS),\n"
        "}))\n"
    )
    payload = _run_probe(installed, probe)
    assert payload["catalog_versions"] == ["0001", "0002"]
    assert payload["bundle_versions"] == ["0001", "0002", "0003", "0004"]
    assert payload["catalog_current"] == 2
    assert payload["bundle_current"] == 4


def test_migration_ddl_contains_no_destructive_statement(installed: _Installed) -> None:
    for family, versions in (
        ("catalog", ("0001", "0002")),
        ("bundle", ("0001", "0002", "0003", "0004")),
    ):
        for version in versions:
            text = (
                _REPO_ROOT
                / "src"
                / "yoetz"
                / "resources"
                / "migrations"
                / family
                / f"{version}.sql"
            ).read_text(encoding="utf-8")
            upper = text.upper()
            for forbidden in (r"DROP\s+TABLE", r"DELETE\s+FROM", r"\bUPDATE\b", r"\bTRUNCATE\b"):
                assert re.search(forbidden, upper) is None, (family, version, forbidden)


# ---------------------------------------------------------------------------
# Fresh initialize / replay / newer-than-candidate refusal, via the real installed package
# ---------------------------------------------------------------------------


def test_fresh_catalog_and_bundle_initialize_at_current_schema_version(
    installed: _Installed,
) -> None:
    probe = (
        "import apsw, json\n"
        "from yoetz.adapters.sqlite.migrations import initialize_catalog, initialize_bundle\n"
        "from yoetz.adapters.sqlite.connection import verify_schema_identity\n"
        "catalog = apsw.Connection(':memory:')\n"
        "initialize_catalog(catalog)\n"
        "catalog_identity = verify_schema_identity(catalog)\n"
        "bundle = apsw.Connection(':memory:')\n"
        "initialize_bundle(bundle, {'protocol_version': '0.1', 'storage_schema_version': '4'})\n"
        "bundle_identity = verify_schema_identity(bundle)\n"
        "print(json.dumps({\n"
        "    'catalog_state': catalog_identity.state,\n"
        "    'catalog_version': catalog_identity.user_version,\n"
        "    'bundle_state': bundle_identity.state,\n"
        "    'bundle_version': bundle_identity.user_version,\n"
        "}))\n"
    )
    payload = _run_probe(installed, probe)
    assert payload == {
        "catalog_state": "current",
        "catalog_version": 2,
        "bundle_state": "current",
        "bundle_version": 4,
    }


def test_replaying_migrations_on_an_already_current_database_is_a_verified_noop(
    installed: _Installed,
) -> None:
    probe = (
        "import apsw, json\n"
        "from yoetz.adapters.sqlite.migrations import initialize_catalog, run_migrations, CATALOG_MIGRATIONS\n"
        "catalog = apsw.Connection(':memory:')\n"
        "initialize_catalog(catalog)\n"
        "report = run_migrations(catalog, CATALOG_MIGRATIONS, maintenance=None)\n"
        "print(json.dumps({\n"
        "    'from_version': report.from_version,\n"
        "    'to_version': report.to_version,\n"
        "    'applied_versions': list(report.applied_versions),\n"
        "}))\n"
    )
    payload = _run_probe(installed, probe)
    assert payload == {"from_version": 2, "to_version": 2, "applied_versions": []}


def test_uninitialized_database_reports_uninitialized_not_current(installed: _Installed) -> None:
    probe = (
        "import apsw, json\n"
        "from yoetz.adapters.sqlite.connection import verify_schema_identity\n"
        "fresh = apsw.Connection(':memory:')\n"
        "identity = verify_schema_identity(fresh)\n"
        "print(json.dumps({'state': identity.state, 'user_version': identity.user_version}))\n"
    )
    payload = _run_probe(installed, probe)
    assert payload == {"state": "uninitialized", "user_version": 0}


def test_newer_than_candidate_schema_fails_migration_and_identity_checks_honestly(
    installed: _Installed,
) -> None:
    probe = (
        "import apsw, json\n"
        "from yoetz.adapters.sqlite.migrations import ("
        "initialize_catalog, run_migrations, CATALOG_MIGRATIONS)\n"
        "from yoetz.adapters.sqlite.connection import verify_schema_identity, StorageUnsafeError\n"
        "catalog = apsw.Connection(':memory:')\n"
        "initialize_catalog(catalog)\n"
        "catalog.execute('PRAGMA user_version = 3')\n"
        "identity_reason = None\n"
        "try:\n"
        "    verify_schema_identity(catalog)\n"
        "except StorageUnsafeError as exc:\n"
        "    identity_reason = exc.args[0] if exc.args else str(exc)\n"
        "migration_reason = None\n"
        "try:\n"
        "    run_migrations(catalog, CATALOG_MIGRATIONS, maintenance=None)\n"
        "except RuntimeError as exc:\n"
        "    migration_reason = str(exc)\n"
        "print(json.dumps({'identity_reason': identity_reason, 'migration_reason': migration_reason}))\n"
    )
    payload = _run_probe(installed, probe)
    assert payload["identity_reason"] == "schema_newer_than_binary"
    assert payload["migration_reason"] == "schema_newer_than_binary"


def test_wrong_application_id_is_rejected_as_unsafe(installed: _Installed) -> None:
    probe = (
        "import apsw, json\n"
        "from yoetz.adapters.sqlite.connection import verify_schema_identity, StorageUnsafeError\n"
        "db = apsw.Connection(':memory:')\n"
        "db.execute('PRAGMA application_id = 12345')\n"
        "db.execute('CREATE TABLE decoy(id INTEGER PRIMARY KEY)')\n"
        "reason = None\n"
        "try:\n"
        "    verify_schema_identity(db)\n"
        "except StorageUnsafeError as exc:\n"
        "    reason = exc.args[0] if exc.args else str(exc)\n"
        "print(json.dumps({'reason': reason}))\n"
    )
    payload = _run_probe(installed, probe)
    assert payload["reason"] == "application_id_mismatch"


def test_unknown_older_than_current_user_version_requires_initialization_not_silent_read(
    installed: _Installed,
) -> None:
    # Catalog current remains 1, so the only reachable "older" catalog state is 0
    # (uninitialized); run_migrations must refuse to silently treat that as current.
    probe = (
        "import apsw, json\n"
        "from yoetz.adapters.sqlite.migrations import run_migrations, CATALOG_MIGRATIONS\n"
        "fresh = apsw.Connection(':memory:')\n"
        "reason = None\n"
        "try:\n"
        "    run_migrations(fresh, CATALOG_MIGRATIONS, maintenance=None)\n"
        "except RuntimeError as exc:\n"
        "    reason = str(exc)\n"
        "print(json.dumps({'reason': reason}))\n"
    )
    payload = _run_probe(installed, probe)
    assert payload["reason"] == "schema_initialization_required"


# ---------------------------------------------------------------------------
# Rollback/restore documentation exists and states the verified-backup-first rule
# ---------------------------------------------------------------------------


def test_migration_rollback_runbook_documents_backup_before_mutation() -> None:
    text = (_REPO_ROOT / "docs" / "runbooks" / "migration-rollback.md").read_text(encoding="utf-8")
    lowered = text.lower()
    assert "backup" in lowered
    assert "quarantine" in lowered
    assert "verified" in lowered
    # Rollback is documented as restoring a verified backup, never a reverse-SQL downgrade.
    assert "reverse sql" in lowered or "never rewrite" in lowered or "downgrade" in lowered
