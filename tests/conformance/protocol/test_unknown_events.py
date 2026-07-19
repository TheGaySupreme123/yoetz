"""Opaque unknown-event preservation through domain construction and replay."""

from __future__ import annotations

from typing import Any, cast

from builders.replay import replay_records
from fixture_loader import FixtureLoader
from yoetz.domain.events import (
    EVENT_FAMILIES,
    AcceptedEvent,
    EventDraft,
    EventSchema,
    UnknownEvent,
    accepted_record_to_json,
)
from yoetz.domain.values import event_id, freeze_json, timestamp_from_string
from yoetz.kernel.projections import projection_digest, projection_snapshot
from yoetz.kernel.reducers import replay
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.coverage import LedgerFreshness


def _fixture(loader: FixtureLoader) -> dict[str, Any]:
    return cast(dict[str, Any], loader.load_json("replay/unknown-schema.case.json"))


def test_unknown_event_round_trip_preserves_opaqueness(fixture_loader: FixtureLoader) -> None:
    document = _fixture(fixture_loader)
    expected = cast(dict[str, Any], document["expected"])
    opaque = cast(dict[str, Any], expected["opaque_unknown"])
    records = replay_records("unknown-schema")
    unknown = records[1]

    assert type(unknown) is UnknownEvent
    unknown_record = unknown
    assert unknown_record.projection_status == "unknown_unprojected"
    assert unknown_record.event_id == opaque["event_id"]
    assert unknown_record.entry_digest == opaque["entry_digest"]
    assert unknown_record.canonical_payload_digest == opaque["canonical_payload_digest"]
    assert (
        canonical_encode(cast(JsonValue, unknown_record.payload)).hex()
        == opaque["canonical_payload_hex"]
    )
    assert (
        canonical_digest(cast(JsonValue, unknown_record.payload))
        == opaque["canonical_payload_digest"]
    )
    assert "payload" not in accepted_record_to_json(unknown_record)


def test_unknown_event_adds_projection_gap_only(fixture_loader: FixtureLoader) -> None:
    document = _fixture(fixture_loader)
    expected = cast(dict[str, Any], document["expected"])
    expected_projection = cast(dict[str, Any], expected["final_projection"])
    expected_snapshot = cast(dict[str, JsonValue], expected_projection["snapshot"])
    records = replay_records("unknown-schema")

    before_unknown = replay(records[:1])
    through_unknown = replay(records[:2])
    final = replay(records)

    assert through_unknown.unknown_event_count == before_unknown.unknown_event_count + 1
    assert through_unknown.freshness is LedgerFreshness.PARTIAL
    assert through_unknown.plans == before_unknown.plans
    assert through_unknown.obligations == before_unknown.obligations
    assert through_unknown.actions == before_unknown.actions
    assert len(through_unknown.coverage_gaps) == 1
    assert final.unknown_event_count == 1
    assert final.freshness is LedgerFreshness.PARTIAL
    assert len(final.obligations) == 1
    assert len(final.actions) == 1
    assert canonical_encode(projection_snapshot(final)) == canonical_encode(expected_snapshot)
    assert projection_digest(final) == expected_projection["digest"]


def test_unknown_version_or_type_batch_rejects_or_preserves_as_specified() -> None:
    known_records = replay_records("all-event-families")
    assert all(type(record) is AcceptedEvent for record in known_records)
    assert {cast(AcceptedEvent, record).schema.name for record in known_records} == set(
        EVENT_FAMILIES
    )

    unknown_payload = freeze_json({"looks_known": {"task_title": "must stay opaque"}})
    for schema in (
        EventSchema("future_annotation", "1.0.0"),
        EventSchema("session_opened", "2.0.0"),
    ):
        draft = EventDraft(
            event_id=event_id("evt_00000000-0000-4000-8000-000000000001"),
            schema=schema,
            occurred_at=timestamp_from_string("2026-01-01T00:00:00.000Z"),
            causal_parents=(),
            payload=unknown_payload,
            artifact_refs=(),
            evidence_refs=(),
        )
        assert draft.payload == unknown_payload
        assert type(draft.payload) is type(unknown_payload)
