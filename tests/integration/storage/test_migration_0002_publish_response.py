"""Catalog migration 0002 publish-response storage checks."""

from __future__ import annotations

import apsw

from yoetz.adapters.sqlite.migrations import CATALOG_MIGRATIONS, initialize_catalog, run_migrations


def test_forward_migrate_catalog_0001_to_0002() -> None:
    catalog = apsw.Connection(":memory:")
    catalog.execute("PRAGMA foreign_keys = ON")
    catalog.execute("PRAGMA trusted_schema = OFF")
    with catalog:
        catalog.execute(CATALOG_MIGRATIONS[0].ddl.decode("utf-8"))
        catalog.execute(
            "INSERT INTO catalog_meta(key, value) VALUES('storage_schema_version', '1')"
        )

    report = run_migrations(catalog, CATALOG_MIGRATIONS, maintenance=None)  # type: ignore[arg-type]

    assert report.from_version == 1
    assert report.to_version == 2
    assert report.applied_versions == ("0002",)
    assert catalog.execute("PRAGMA user_version").fetchone() == (2,)
    assert catalog.execute(
        "SELECT value FROM catalog_meta WHERE key = 'storage_schema_version'"
    ).fetchone() == ("2",)
    assert catalog.execute(
        "SELECT strict, wr FROM pragma_table_list WHERE name = 'publish_responses'"
    ).fetchone() == (1, 1)


def test_fresh_catalog_initialization_includes_publish_responses() -> None:
    catalog = apsw.Connection(":memory:")
    initialize_catalog(catalog)

    assert catalog.execute("PRAGMA user_version").fetchone() == (2,)
    assert tuple(row[1] for row in catalog.execute("PRAGMA table_info(publish_responses)")) == (
        "writer_id",
        "request_id",
        "sink",
        "task_id",
        "session_id",
        "request_digest",
        "result_canonical",
        "result_digest",
    )
