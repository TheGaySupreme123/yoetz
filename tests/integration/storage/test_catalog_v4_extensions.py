"""The catalog migration owns the durable lineage and coordination extension tables."""

from __future__ import annotations

import apsw

from yoetz.adapters.sqlite.migrations import initialize_catalog


def test_catalog_v4_installs_provisional_and_coordination_tables() -> None:
    db = apsw.Connection(":memory:")
    try:
        initialize_catalog(db)
        expected = {
            "host_lineage_annotations",
            "host_lineage_annotation_aliases",
            "coordination_detections",
            "coordination_participants",
            "coordination_deliveries",
            "coordination_coverage",
            "coordination_obligations",
            "repository_grouping_preferences",
        }
        actual = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table' AND name IN "
                "('host_lineage_annotations', 'host_lineage_annotation_aliases', "
                "'coordination_detections', 'coordination_participants', "
                "'coordination_deliveries', 'coordination_coverage', 'coordination_obligations', "
                "'repository_grouping_preferences')"
            )
        }
        assert actual == expected
        assert tuple(
            row[1] for row in db.execute("PRAGMA table_info(coordination_obligations)")
        ) == (
            "detection_id",
            "task_id",
            "obligation_id",
            "declared",
            "addressed",
            "resolved",
        )
        assert tuple(row[1] for row in db.execute("PRAGMA table_info(coordination_coverage)")) == (
            "coverage_id",
            "project_id",
            "task_id",
            "membership_generation",
            "coverage",
            "gap_code",
        )
        assert tuple(row[1] for row in db.execute("PRAGMA table_info(lineage_operations)"))[
            -2:
        ] == ("project_id", "membership_generation")
        assert db.pragma("user_version") == 4
        assert db.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        db.close(force=True)
