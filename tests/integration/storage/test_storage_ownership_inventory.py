"""The #496/#498 storage ownership inventory stays complete and discoverable."""

from __future__ import annotations

from pathlib import Path

_INVENTORY = Path(__file__).resolve().parents[3] / "docs" / "storage-ownership.md"


def test_inventory_names_every_workspace_table_and_runtime_owner() -> None:
    text = _INVENTORY.read_text(encoding="utf-8")
    for table in (
        "observation_workspace_bindings",
        "observation_content_manifests",
        "observation_logical_identity",
        "observation_trusted_check_policies",
        "observation_verification_jobs",
        "observation_verification_results",
        "observation_advice_history",
        "observation_advice_delivery",
        "observation_inspection_snapshots",
        "observation_workspace_session_routes",
        "observation_session_advice",
        "observation_cursors",
        "observation_events",
    ):
        assert f"`{table}`" in text
    for table in (
        "task_routes",
        "start_operations",
        "task_sessions",
        "projects",
        "repository_grouping_preferences",
        "project_memberships",
        "coordination_grants",
        "lineage_task_meta",
        "lineage_operations",
        "lineage_attach_handles",
        "lineage_manifests",
        "host_lineage_annotations",
        "host_lineage_annotation_aliases",
        "coordination_detections",
        "coordination_participants",
        "coordination_deliveries",
        "coordination_coverage",
        "coordination_obligations",
    ):
        assert f"`{table}`" in text
    for marker in (
        "durable ownership and retention contract",
        "migrations/catalog/0004.sql",
        "LocalObservationStore",
        "codex-lifecycle/<codex_session_id>.json",
        "| stay; tested",
        "| catalog;",
        "| relocate;",
    ):
        assert marker in text
