"""Frozen SQLite migration registries and fresh-database initialization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from time import monotonic_ns
from typing import TYPE_CHECKING, Final, cast

import apsw

if TYPE_CHECKING:
    from yoetz.ports.maintenance import MaintenanceHandle

YOETZ_APPLICATION_ID: Final = 0x594F4554
EMPTY_PROJECTION_DIGEST: Final = (
    "sha256:0f8ec0c66f196bee631ef5447ef5c914e812fe530ee1f4b7477e24b22a9911c9"
)


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable numbered migration loaded from installed resources."""

    version: str
    ddl: bytes

    def __post_init__(self) -> None:
        if len(self.version) != 4 or not self.version.isascii() or not self.version.isdigit():
            raise ValueError("migration_version_invalid")
        _validate_ddl_bytes(self.ddl)


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """Bounded structural result of a migration registry check or application."""

    from_version: int
    to_version: int
    applied_versions: tuple[str, ...]
    backup_manifest_digest: str | None
    duration_ms: int


def _validate_ddl_bytes(value: bytes) -> None:
    if value.startswith(b"\xef\xbb\xbf"):
        raise ValueError("migration_bom_forbidden")
    if b"\r" in value:
        raise ValueError("migration_line_ending_invalid")
    if not value.endswith(b"\n") or value.endswith(b"\n\n"):
        raise ValueError("migration_final_lf_invalid")
    try:
        value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("migration_utf8_invalid") from error


def _load_resource(family: str, version: str) -> bytes:
    target = resources.files("yoetz").joinpath("resources", "migrations", family, f"{version}.sql")
    value = target.read_bytes()
    _validate_ddl_bytes(value)
    return value


CATALOG_MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration("0001", _load_resource("catalog", "0001")),
    Migration("0002", _load_resource("catalog", "0002")),
    Migration("0003", _load_resource("catalog", "0003")),
)
BUNDLE_MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration("0001", _load_resource("bundle", "0001")),
    Migration("0002", _load_resource("bundle", "0002")),
    Migration("0003", _load_resource("bundle", "0003")),
    Migration("0004", _load_resource("bundle", "0004")),
    Migration("0005", _load_resource("bundle", "0005")),
    Migration("0006", _load_resource("bundle", "0006")),
    Migration("0007", _load_resource("bundle", "0007")),
    Migration("0008", _load_resource("bundle", "0008")),
    Migration("0009", _load_resource("bundle", "0009")),
)


def _validate_registry(registry: Sequence[Migration]) -> None:
    if not registry:
        raise ValueError("migration_registry_empty")
    expected = 1
    seen: set[str] = set()
    for migration in registry:
        if migration.version in seen:
            raise ValueError("migration_version_duplicate")
        if int(migration.version) != expected:
            raise ValueError("migration_version_noncontiguous")
        seen.add(migration.version)
        expected += 1


_validate_registry(CATALOG_MIGRATIONS)
_validate_registry(BUNDLE_MIGRATIONS)


def current_schema_version(registry: Sequence[Migration]) -> int:
    """Return the positive current version after validating registry continuity."""

    _validate_registry(registry)
    return int(registry[-1].version)


def _pragma_int(db: apsw.Connection, name: str) -> int:
    row = db.execute(f"PRAGMA {name}").fetchone()
    if row is None or type(row[0]) is not int:
        raise RuntimeError("schema_pragma_invalid")
    return row[0]


def _require_fresh(db: apsw.Connection) -> None:
    if _pragma_int(db, "user_version") != 0:
        raise RuntimeError("schema_already_initialized")
    row = db.execute(
        "SELECT 1 FROM sqlite_schema "
        "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'index', 'view', 'trigger') LIMIT 1"
    ).fetchone()
    if row is not None:
        raise RuntimeError("schema_objects_preexisting")


def _configure_schema_connection(db: apsw.Connection) -> None:
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA trusted_schema = OFF")
    if _pragma_int(db, "foreign_keys") != 1 or _pragma_int(db, "trusted_schema") != 0:
        raise RuntimeError("schema_security_pragma_mismatch")


def _execute(db: apsw.Connection, migration: Migration) -> None:
    db.execute(migration.ddl.decode("utf-8"))


def _verify_identity(db: apsw.Connection, expected_version: int) -> None:
    if _pragma_int(db, "foreign_keys") != 1 or _pragma_int(db, "trusted_schema") != 0:
        raise RuntimeError("schema_security_pragma_mismatch")
    if _pragma_int(db, "application_id") != YOETZ_APPLICATION_ID:
        raise RuntimeError("schema_application_id_mismatch")
    if _pragma_int(db, "user_version") != expected_version:
        raise RuntimeError("schema_user_version_mismatch")
    violations = db.execute("PRAGMA foreign_key_check").fetchone()
    if violations is not None:
        raise RuntimeError("schema_foreign_key_violation")


def initialize_catalog(db: apsw.Connection) -> None:
    """Install the standalone catalog migration on a fresh staged database."""

    _configure_schema_connection(db)
    _require_fresh(db)
    with db:
        for migration in CATALOG_MIGRATIONS:
            _execute(db, migration)
    _verify_identity(db, current_schema_version(CATALOG_MIGRATIONS))


def initialize_bundle(db: apsw.Connection, bundle_meta_seed: Mapping[str, str]) -> None:
    """Install and seed the standalone task-bundle migration atomically."""

    _configure_schema_connection(db)
    _require_fresh(db)
    seed = dict(bundle_meta_seed)
    if any(type(key) is not str or type(value) is not str for key, value in seed.items()):
        raise ValueError("bundle_meta_seed_invalid")
    if "import_schema_version" in seed and seed["import_schema_version"] != "1":
        raise ValueError("import_schema_version_mismatch")
    seed["import_schema_version"] = "1"
    target_version = current_schema_version(BUNDLE_MIGRATIONS)
    seed["storage_schema_version"] = str(target_version)

    with db:
        for migration in BUNDLE_MIGRATIONS:
            _execute(db, migration)
        db.executemany(
            "INSERT INTO bundle_meta(key, value) VALUES (?, ?)",
            sorted(seed.items()),
        )
        db.execute("INSERT INTO counters(name, next_value) VALUES ('ingestion_sequence', 1)")
        db.execute(
            "INSERT INTO projection_state("
            "projection_name, projection_version, projection_generation, "
            "applied_through_seq, state_digest, engine_version"
            ") VALUES ('work', 'yoetz/0.1.0', 1, 0, ?, '0.1.0')",
            (EMPTY_PROJECTION_DIGEST,),
        )
        db.execute(
            "INSERT INTO p1_projection_state("
            "projection_name, frontier_seq, head_digest, "
            "task_title_source_event_id, current_plan_source_event_id, "
            "open_obligation_count, unresolved_finding_count, "
            "status_coverage_canonical, status_gap_codes_canonical, "
            "latest_check_event_id, latest_subject_frontier_seq, "
            "latest_subject_frontier_digest, latest_verdict, "
            "latest_returned_finding_ids, latest_suppressed_count, "
            "latest_coverage_canonical, freshness, unknown_event_count"
            ") VALUES ("
            "'work', 0, 'genesis', NULL, NULL, 0, 0, NULL, NULL, "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, 'unknown', 0"
            ")"
        )
        db.execute(
            "INSERT INTO p1_query_snapshots("
            "valid_from_seq, valid_to_seq, head_digest, "
            "task_title_source_event_id, current_plan_source_event_id, "
            "open_obligation_count, unresolved_finding_count, freshness, "
            "coverage_canonical, gap_codes_canonical"
            ") VALUES (0, NULL, 'genesis', NULL, NULL, 0, 0, 'unknown', NULL, NULL)"
        )
    _verify_identity(db, current_schema_version(BUNDLE_MIGRATIONS))


def run_migrations(
    db: apsw.Connection,
    registry: Sequence[Migration],
    *,
    maintenance: MaintenanceHandle | None,
) -> MigrationReport:
    """Return the bounded migration result for an already initialized database."""

    del maintenance
    started = monotonic_ns()
    _configure_schema_connection(db)
    target = current_schema_version(registry)
    current = _pragma_int(db, "user_version")
    if current == 0:
        raise RuntimeError("schema_initialization_required")
    if current > target:
        raise RuntimeError("schema_newer_than_binary")
    applied: list[str] = []
    if current < target:
        pending = tuple(item for item in registry if int(item.version) > current)
        if not pending or int(pending[0].version) != current + 1:
            raise RuntimeError("schema_version_unknown")
        with db:
            for migration in pending:
                _execute(db, migration)
                applied.append(migration.version)
            tables = {
                cast(str, row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_schema "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if "bundle_meta" in tables:
                db.execute(
                    "INSERT INTO bundle_meta(key, value) VALUES('storage_schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(target),),
                )
            elif "catalog_meta" in tables:
                db.execute(
                    "INSERT INTO catalog_meta(key, value) VALUES('storage_schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(target),),
                )
        current = _pragma_int(db, "user_version")
    _verify_identity(db, target)
    elapsed_ms = max(0, (monotonic_ns() - started) // 1_000_000)
    return MigrationReport(
        from_version=current if not applied else int(applied[0]) - 1,
        to_version=target,
        applied_versions=tuple(applied),
        backup_manifest_digest=None,
        duration_ms=elapsed_ms,
    )


__all__ = [
    "BUNDLE_MIGRATIONS",
    "CATALOG_MIGRATIONS",
    "Migration",
    "MigrationReport",
    "current_schema_version",
    "initialize_bundle",
    "initialize_catalog",
    "run_migrations",
]
