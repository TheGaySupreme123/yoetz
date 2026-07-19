"""Property checks for replay partition equivalence and weakening-only gaps."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from builders.replay import replay_records
from yoetz.kernel.projections import ProjectionState, empty_projection_state
from yoetz.kernel.reducers import empty_replay_index, extend_replay_index, reduce_event, replay
from yoetz.protocol.coverage import LEDGER_FRESHNESS_ORDER

_FIXTURES = (
    "all-event-families",
    "multi-writer",
    "projection-rebuild",
    "supersession-redaction",
    "unknown-schema",
    "wall-clock-reversal",
)


def _partitioned_replay(name: str, chunk_sizes: tuple[int, ...]) -> ProjectionState:
    records = replay_records(name)
    state = empty_projection_state()
    index = empty_replay_index()
    position = 0
    for size in chunk_sizes:
        for record in records[position : position + size]:
            index = extend_replay_index(index, record)
            state = reduce_event(state, record, index)
        position += size
    for record in records[position:]:
        index = extend_replay_index(index, record)
        state = reduce_event(state, record, index)
    return state


@given(
    st.sampled_from(_FIXTURES),
    st.lists(st.integers(min_value=0, max_value=20), min_size=0, max_size=20).map(tuple),
)
@settings(deadline=None, max_examples=15)
def test_full_vs_partitioned_replay_match(name: str, chunk_sizes: tuple[int, ...]) -> None:
    assert _partitioned_replay(name, chunk_sizes) == replay(replay_records(name))


@given(st.sampled_from(_FIXTURES))
@settings(deadline=None, max_examples=12)
def test_incremental_replay_matches_reference_model(name: str) -> None:
    records = replay_records(name)
    state = empty_projection_state()
    index = empty_replay_index()
    for position, record in enumerate(records, start=1):
        index = extend_replay_index(index, record)
        previous = state
        state = reduce_event(state, record, index)
        assert state == replay(records[:position])
        assert reduce_event(previous, record, index) == state


def test_unknown_event_and_redaction_paths_weaken_only() -> None:
    unknown = replay(replay_records("unknown-schema"))
    assert unknown.unknown_event_count == 1
    assert any(marker.startswith("unknown_event:") for marker in unknown.coverage_gaps)

    records = replay_records("all-event-families")
    redaction_position = next(
        index for index, record in enumerate(records) if record.schema.name == "redaction_recorded"
    )
    before = replay(records[:redaction_position])
    after = replay(records[: redaction_position + 1])
    assert LEDGER_FRESHNESS_ORDER[after.freshness] <= LEDGER_FRESHNESS_ORDER[before.freshness]
    assert set(after.actions) == set(before.actions)
    assert set(after.results) == set(before.results)
    assert set(after.evidence) == set(before.evidence)
    assert set(after.claims) == set(before.claims)
    assert set(after.findings) == set(before.findings)
    assert any(marker.startswith("redacted_") for marker in after.coverage_gaps)
