"""Canonical check outcomes preserve legacy bytes and cannot turn unassessable into proof."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yoetz.domain.events import CheckRecordedPayload, EventSchema, decode_payload, encode_payload
from yoetz.domain.values import freeze_json
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.schemas import validate_schema_instance


def _vectors() -> list[dict[str, Any]]:
    path = (
        Path(__file__).resolve().parents[3] / "fixtures/canonical/check-conclusion-1.3.0.case.json"
    )
    return json.loads(path.read_bytes())["input"]["vectors"]


def test_golden_check_outcomes_round_trip_through_schema_and_domain() -> None:
    for vector in _vectors():
        version, wire = vector["schema_version"], vector["payload"]
        validate_schema_instance("check-recorded", version, wire)
        payload = decode_payload(EventSchema("check_recorded", version), wire)
        assert type(payload) is CheckRecordedPayload
        assert payload.semantic_conclusion == wire.get("semantic_conclusion")
        encoded = encode_payload(payload)
        assert canonical_encode(encoded).hex() == vector["canonical_hex"]
        assert canonical_digest(encoded) == vector["digest"]


def test_conclusion_is_not_admitted_on_legacy_version_or_absent_on_new_version() -> None:
    current = _vectors()[1]["payload"]
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("check_recorded", "1.2.0"), current)
    missing = {key: value for key, value in current.items() if key != "semantic_conclusion"}
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("check_recorded", "1.3.0"), freeze_json(missing))
    for value in ("completed", "", 1):
        with pytest.raises(ProtocolValueError):
            decode_payload(
                EventSchema("check_recorded", "1.3.0"),
                freeze_json({**current, "semantic_conclusion": value}),
            )


def test_check_change_files_ride_only_a_recorded_conclusion_and_stay_closed() -> None:
    """ADR-031 (#883): keyed shown-file commitments, never paths, only beside a conclusion."""

    vector = _vectors()[-1]
    wire = vector["payload"]
    payload = decode_payload(EventSchema("check_recorded", "1.3.0"), freeze_json(wire))
    assert type(payload) is CheckRecordedPayload
    assert payload.check_change_files is not None
    assert payload.check_change_files.fully_shown == tuple(
        wire["check_change_files"]["fully_shown"]
    )
    assert {
        item.commitment: (
            item.shown_bytes,
            item.redactions,
            item.section_admitted,
            item.clean_bytes,
        )
        for item in payload.check_change_files.partially_shown
    } == {
        item["commitment"]: (
            item["shown_bytes"],
            item["redactions"],
            item["section_admitted"],
            item["clean_bytes"],
        )
        for item in wire["check_change_files"]["partially_shown"]
    }
    files = wire["check_change_files"]
    without_conclusion = {key: value for key, value in wire.items() if key != "semantic_conclusion"}
    invalid = [
        (EventSchema("check_recorded", "1.2.0"), without_conclusion),
        (
            EventSchema("check_recorded", "1.3.0"),
            {
                **wire,
                "check_change_files": {
                    **files,
                    "partially_shown": [
                        {
                            "clean_bytes": 1,
                            "commitment": files["fully_shown"][0],
                            "redactions": 0,
                            "section_admitted": False,
                            "shown_bytes": 1,
                        }
                    ],
                },
            },
        ),
        (
            EventSchema("check_recorded", "1.3.0"),
            {
                **wire,
                "check_change_files": {
                    **files,
                    "partially_shown": [{**files["partially_shown"][0], "shown_bytes": -1}],
                },
            },
        ),
        (
            EventSchema("check_recorded", "1.3.0"),
            {
                **wire,
                "check_change_files": {
                    **files,
                    # A clean prefix longer than what was shown.
                    "partially_shown": [{**files["partially_shown"][0], "clean_bytes": 2_000}],
                },
            },
        ),
        (
            EventSchema("check_recorded", "1.3.0"),
            {
                **wire,
                "check_change_files": {
                    **files,
                    "partially_shown": [
                        {
                            key: value
                            for key, value in files["partially_shown"][0].items()
                            if key != "redactions"
                        }
                    ],
                },
            },
        ),
        (
            EventSchema("check_recorded", "1.3.0"),
            {
                **wire,
                "check_change_files": {
                    **files,
                    "partially_shown": [files["partially_shown"][0]["commitment"]],
                },
            },
        ),
        (
            EventSchema("check_recorded", "1.3.0"),
            {**wire, "check_change_files": {**files, "fully_shown": ["src/app.py"]}},
        ),
        (
            EventSchema("check_recorded", "1.3.0"),
            {**wire, "check_change_files": {**files, "extra": 1}},
        ),
    ]
    for schema, candidate in invalid:
        with pytest.raises(ProtocolValueError):
            decode_payload(schema, freeze_json(candidate))
    # A record past its file bound keeps the files it holds: every entry is still true.
    bounded = {**wire, "check_change_files": {**files, "complete": False}}
    validate_schema_instance("check-recorded", "1.3.0", bounded)
    decoded = decode_payload(EventSchema("check_recorded", "1.3.0"), freeze_json(bounded))
    assert type(decoded) is CheckRecordedPayload and decoded.check_change_files is not None
    assert decoded.check_change_files.complete is False
    assert decoded.check_change_files.fully_shown == tuple(files["fully_shown"])
