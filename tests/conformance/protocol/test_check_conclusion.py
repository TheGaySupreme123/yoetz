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
        included = wire.get("semantic_included_refs")
        assert payload.semantic_included_refs == (None if included is None else tuple(included))
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


def test_included_references_bind_to_a_reduced_recorded_conclusion() -> None:
    """Issue #904: only a reduced review with a recorded conclusion carries included references."""

    [reduced] = [
        item["payload"] for item in _vectors() if "semantic_included_refs" in item["payload"]
    ]
    refs: list[str] = list(reduced["semantic_included_refs"])
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("check_recorded", "1.2.0"), freeze_json(reduced))
    unreduced: dict[str, Any] = {
        **reduced,
        "coverage": {**reduced["coverage"], "known_gaps": ["content_unselected"]},
    }
    empty: list[str] = []
    invalid_payloads: list[dict[str, Any]] = [
        unreduced,
        {**reduced, "semantic_included_refs": empty},
        {**reduced, "semantic_included_refs": list(reversed(refs))},
        {**reduced, "semantic_included_refs": ["not-a-ref"]},
    ]
    for invalid in invalid_payloads:
        with pytest.raises(ProtocolValueError):
            decode_payload(EventSchema("check_recorded", "1.3.0"), freeze_json(invalid))
