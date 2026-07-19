"""Fresh-database and frozen importer-schema checks for migration 0001."""

from pathlib import Path

import apsw
import pytest

from yoetz.adapters.sqlite.migrations import initialize_bundle, initialize_catalog

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("family", ["catalog", "bundle"])
def test_root_and_installed_migration_resources_are_byte_identical(family: str) -> None:
    root = ROOT / "migrations" / family / "0001.sql"
    resource = ROOT / "src" / "yoetz" / "resources" / "migrations" / family / "0001.sql"

    assert root.read_bytes() == resource.read_bytes()


def test_fresh_migrations_install_identified_foreign_key_clean_schemas() -> None:
    catalog = apsw.Connection(":memory:")
    initialize_catalog(catalog)

    bundle = apsw.Connection(":memory:")
    initialize_bundle(bundle, {"task_id": "task_test", "owner_generation": "generation_test"})

    for database in (catalog, bundle):
        assert database.execute("PRAGMA application_id").fetchone() == (0x594F4554,)
        assert database.execute("PRAGMA user_version").fetchone() == (1,)
        assert database.execute("PRAGMA foreign_keys").fetchone() == (1,)
        assert database.execute("PRAGMA trusted_schema").fetchone() == (0,)
        assert database.execute("PRAGMA foreign_key_check").fetchone() is None

    assert bundle.execute(
        "SELECT value FROM bundle_meta WHERE key = 'import_schema_version'"
    ).fetchone() == ("1",)


def test_importer_tables_have_frozen_columns_indexes_and_no_triggers() -> None:
    database = apsw.Connection(":memory:")
    initialize_bundle(database, {})

    expected_columns = {
        "import_jobs": tuple(
            "source_identity_digest task_id session_id source_commitment "
            "codex_capability_profile_id mapping_version publishing_writer_id "
            "source_object_id capture_metadata_object_id "
            "capture_metadata_object_commitment source_byte_count source_line_count "
            "source_final_newline codex_version source_kind source_exit_status "
            "stderr_present stderr_captured_byte_count stderr_truncated stderr_commitment "
            "metadata_digest state phase job_revision owner_generation lease_owner_id "
            "lease_generation lease_expires_at plan_digest batch_count completed_batch_count "
            "report_request_id report_event_id report_evidence_id report_object_id "
            "report_digest report_result_canonical report_result_digest "
            "report_evidence_draft_canonical report_evidence_draft_digest "
            "report_append_result_canonical report_append_result_digest report_ingestion_seq "
            "report_entry_digest terminal_result_canonical terminal_result_digest "
            "quarantine_code terminal_at created_at updated_at".split()
        ),
        "import_request_aliases": (
            "requesting_writer_id",
            "request_id",
            "request_digest",
            "source_identity_digest",
            "created_at",
        ),
        "import_batches": (
            "source_identity_digest",
            "batch_index",
            "state",
            "request_id",
            "plan_object_id",
            "plan_object_commitment",
            "plan_digest",
            "event_ids_canonical",
            "event_ids_digest",
            "event_count",
            "append_result_canonical",
            "append_result_digest",
            "subject_frontier_seq",
            "subject_frontier_digest",
            "result_frontier_seq",
            "result_frontier_digest",
            "first_ingestion_seq",
            "last_ingestion_seq",
            "completed_at",
            "created_at",
            "updated_at",
        ),
        "import_publication_requests": (
            "publishing_writer_id",
            "request_id",
            "source_identity_digest",
            "publication_ordinal",
        ),
    }
    for table, expected in expected_columns.items():
        actual = tuple(row[1] for row in database.execute(f"PRAGMA table_info({table})"))
        assert actual == expected

    indexes = {
        row[0]
        for row in database.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert {
        "import_request_aliases_source",
        "import_jobs_session_state",
        "import_jobs_session_terminal",
        "import_batches_next",
    } <= indexes
    assert (
        database.execute(
            "SELECT name FROM sqlite_schema WHERE type IN ('trigger', 'view')"
        ).fetchall()
        == []
    )


def test_importer_source_reserved_shape_and_publication_reservations_are_enforced() -> None:
    database = apsw.Connection(":memory:")
    initialize_bundle(database, {})
    database.execute(
        "INSERT INTO writers VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("writer", "task", "session", 1, "genesis", "active", "2026-07-19T00:00:00.000Z"),
    )
    for object_id in ("source", "manifest"):
        database.execute(
            "INSERT INTO objects VALUES (?, ?, 0, ?, ?, ?, ?, 'present', ?)",
            (
                object_id,
                "import_source",
                "commitment",
                "envelope",
                "v1",
                "slot",
                "2026-07-19T00:00:00.000Z",
            ),
        )

    columns = (
        "source_identity_digest",
        "task_id",
        "session_id",
        "source_commitment",
        "codex_capability_profile_id",
        "mapping_version",
        "publishing_writer_id",
        "source_object_id",
        "capture_metadata_object_id",
        "capture_metadata_object_commitment",
        "source_byte_count",
        "source_line_count",
        "source_final_newline",
        "codex_version",
        "source_kind",
        "source_exit_status",
        "stderr_present",
        "stderr_captured_byte_count",
        "stderr_truncated",
        "stderr_commitment",
        "metadata_digest",
        "state",
        "phase",
        "job_revision",
        "owner_generation",
        "lease_owner_id",
        "lease_generation",
        "lease_expires_at",
        "plan_digest",
        "batch_count",
        "completed_batch_count",
        "report_request_id",
        "report_event_id",
        "report_evidence_id",
        "report_object_id",
        "report_digest",
        "report_result_canonical",
        "report_result_digest",
        "report_evidence_draft_canonical",
        "report_evidence_draft_digest",
        "report_append_result_canonical",
        "report_append_result_digest",
        "report_ingestion_seq",
        "report_entry_digest",
        "terminal_result_canonical",
        "terminal_result_digest",
        "quarantine_code",
        "terminal_at",
        "created_at",
        "updated_at",
    )
    values = (
        "source-id",
        "task",
        "session",
        "source-commitment",
        "profile",
        "mapping-v1",
        "writer",
        "source",
        "manifest",
        "manifest-commitment",
        0,
        0,
        0,
        "codex-v",
        "file",
        None,
        0,
        0,
        0,
        None,
        "metadata",
        "pending",
        "source_reserved",
        0,
        "generation",
        "lease",
        1,
        "2026-07-19T00:01:00.000Z",
        None,
        0,
        0,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        "2026-07-19T00:00:00.000Z",
        "2026-07-19T00:00:00.000Z",
    )
    placeholders = ", ".join("?" for _ in columns)
    database.execute(
        f"INSERT INTO import_jobs ({', '.join(columns)}) VALUES ({placeholders})", values
    )

    database.execute(
        "INSERT INTO import_publication_requests VALUES ('writer', 'request-0', 'source-id', 0)"
    )
    with pytest.raises(apsw.ConstraintError):
        database.execute(
            "INSERT INTO import_publication_requests VALUES ('writer', 'request-0', 'source-id', 1)"
        )
    with pytest.raises(apsw.ConstraintError):
        database.execute(
            "INSERT INTO import_publication_requests VALUES ('writer', 'request-1', 'source-id', 0)"
        )
    with pytest.raises(apsw.ConstraintError):
        database.execute(
            "UPDATE import_jobs SET phase = 'publishing' WHERE source_identity_digest = 'source-id'"
        )
