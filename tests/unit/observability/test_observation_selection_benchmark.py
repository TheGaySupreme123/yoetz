"""Focused contract tests for the public-safe issue #687 replay harness."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[3] / "scripts" / "benchmark_observation_selection.py"
_SPEC = importlib.util.spec_from_file_location("benchmark_observation_selection", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

PROTECTED_CLASSES = _MODULE.PROTECTED_CLASSES
latency_summary = _MODULE.latency_summary
run_workload = _MODULE.run_workload
selection_matrix = _MODULE.selection_matrix
synthetic_events = _MODULE.synthetic_events


def test_synthetic_workload_is_deterministic_and_contains_protected_cases() -> None:
    first = synthetic_events(48, fanout=4)
    second = synthetic_events(48, fanout=4)

    assert first == second
    assert {event.category for event in first} >= set(PROTECTED_CLASSES)
    assert {event.session_id for event in first} == {
        "bench687-session-0000",
        "bench687-session-0001",
        "bench687-session-0002",
        "bench687-session-0003",
    }
    for event in first:
        payload = event.hook_payload()
        assert not {"content", "stdout", "stderr", "transcript", "prompt"} & payload.keys()
        if event.phase == "PreToolUse":
            assert event.capture_proxy_bytes == 0
        else:
            assert event.capture_proxy_bytes >= 0


def test_synthetic_workload_rejects_unbounded_shape_inputs() -> None:
    with pytest.raises(ValueError, match="positive"):
        synthetic_events(0)
    with pytest.raises(ValueError, match="positive"):
        synthetic_events(1, fanout=0)


def test_latency_summary_is_bounded_and_has_tail_fields() -> None:
    summary = latency_summary([4.0, 1.0, 3.0, 2.0])

    assert summary["count"] == 4.0
    assert summary["p50_ms"] <= summary["p95_ms"] <= summary["p99_ms"] <= summary["max_ms"]
    assert latency_summary([]) == {
        "count": 0.0,
        "p50_ms": 0.0,
        "p95_ms": 0.0,
        "p99_ms": 0.0,
        "max_ms": 0.0,
    }


def test_replay_reports_admission_and_capture_proxy_without_plaintext_capture() -> None:
    report = run_workload(3, fanout=2, revision="e56f7d0a")

    assert report["revision"] == "e56f7d0a"
    assert report["workload"]["attempted_event_count"] == 3
    assert report["workload"]["claimed_event_count"] == 3
    assert report["hook"]["append_success_count"] == 3
    assert report["capture_proxy"]["native_capture_executed"] is False
    assert report["capture_proxy"]["plaintext_retained"] is False
    assert report["capture_proxy"]["eligible_bytes_total"] > 0
    assert report["storage"]["state_write_count"] > 0
    assert report["admission"]["pending_outbox_rows"] <= report["baseline_limits"]["outbox_rows"]


def test_selection_matrix_has_all_independent_mode_capacity_pairs() -> None:
    pairs = {(selection.detail.value, int(selection.capacity)) for selection in selection_matrix()}

    assert pairs == {
        ("focused", 512),
        ("focused", 2_048),
        ("focused", 8_192),
        ("detailed", 512),
        ("detailed", 2_048),
        ("detailed", 8_192),
    }


@pytest.mark.parametrize(
    ("selection_index", "expected_capacity"),
    [
        (0, 512),
        (1, 2_048),
        (2, 8_192),
        (3, 512),
        (4, 2_048),
        (5, 8_192),
    ],
)
def test_selected_capacity_is_applied_to_disposable_session(
    selection_index: int, expected_capacity: int
) -> None:
    report = run_workload(8, fanout=2, selection=selection_matrix()[selection_index])
    selection = report["selection"]
    assert selection is not None
    runtime_status = selection["runtime_status"]

    assert selection["capacity"] == expected_capacity
    assert selection["capacity_enforced"] is True
    assert runtime_status["selected_capacity"] == expected_capacity
    assert runtime_status["effective_capacity"] == expected_capacity


def test_selected_replay_preserves_protected_categories_and_reports_summary_coverage() -> None:
    focused = run_workload(48, fanout=4, selection=selection_matrix()[0])
    selection = focused["selection"]
    assert selection is not None
    attempted = focused["workload"]["attempted_by_class"]
    delivered = selection["deliveries_by_class"]

    assert selection["detail"] == "focused"
    assert selection["capture_extraction_executed"] is False
    assert selection["accounting"]["summary_record_count"] > 0
    for category in PROTECTED_CLASSES:
        if attempted.get(category, 0):
            assert delivered.get(category, 0) > 0
            if category != "closure_mutation":
                assert delivered.get(category, 0) == attempted[category]

    detailed = run_workload(48, fanout=4, selection=selection_matrix()[3])
    detailed_selection = detailed["selection"]
    assert detailed_selection is not None
    assert detailed_selection["detail"] == "detailed"
    assert detailed_selection["accounting"]["summary_record_count"] == 0
    assert detailed_selection["capture_roles_by_event"].get("tool_output", 0) > 0
    assert detailed_selection["capture_roles_by_event"].get("tool_input", 0) == 0
    assert detailed_selection["capture_candidate_bytes"] > 0
    assert detailed_selection["replayable_rejections"] == detailed_selection["commit_failures"]
    assert focused["selection"]["capture_roles_by_event"].get("none", 0) > 0


@pytest.mark.parametrize("host", ("codex", "claude", "cursor"))
def test_selected_replay_accepts_each_synthetic_host_contract(host: str) -> None:
    report = run_workload(48, fanout=4, host=host, selection=selection_matrix()[0])

    assert report["host"] == host
    assert report["workload"]["claimed_event_count"] == 48
    assert report["selection"]["commit_failures"] == 0
    assert report["selection"]["flush_ok"] is True
