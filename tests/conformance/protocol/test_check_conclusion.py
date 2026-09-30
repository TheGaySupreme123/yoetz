"""Canonical check outcomes preserve legacy bytes and cannot turn unassessable into proof."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yoetz.domain.events import (
    CheckRecordedPayload,
    EventSchema,
    check_event_schema,
    decode_payload,
    encode_payload,
)
from yoetz.domain.values import freeze_json
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.schemas import SchemaInstanceInvalid, validate_schema_instance


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


def test_rulings_and_named_missing_items_share_one_check_version() -> None:
    """Issues #905 and #907 fold into ``check_recorded`` 1.4.0: either field, or both."""

    missing_only: dict[str, Any] = next(
        vector["payload"]
        for vector in _vectors()
        if vector["schema_version"] == "1.4.0" and "missing_for_assessment" in vector["payload"]
    )
    rulings: list[dict[str, Any]] = [
        {
            "cited_refs": [],
            "finding_id": "fnd_30000000-0000-4000-8000-000000000001",
            "verdict": "unassessable",
        }
    ]
    both: dict[str, Any] = {**missing_only, "prior_finding_verdicts": rulings}
    validate_schema_instance("check-recorded", "1.4.0", both)
    payload = decode_payload(EventSchema("check_recorded", "1.4.0"), freeze_json(both))
    assert type(payload) is CheckRecordedPayload
    assert payload.prior_finding_verdicts and payload.missing_for_assessment
    assert (
        check_event_schema(
            payload.semantic_conclusion,
            payload.prior_finding_verdicts,
            payload.missing_for_assessment,
        )
        == "1.4.0"
    )
    # Named missing items belong only to an insufficient_packet conclusion, in schema and domain.
    other: dict[str, Any] = {**both, "semantic_conclusion": "no_material_discrepancy"}
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance("check-recorded", "1.4.0", other)
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("check_recorded", "1.4.0"), freeze_json(other))
    # Rulings alone may accompany any completed conclusion.
    ruled: dict[str, Any] = {
        key: value for key, value in other.items() if key != "missing_for_assessment"
    }
    validate_schema_instance("check-recorded", "1.4.0", ruled)
    decode_payload(EventSchema("check_recorded", "1.4.0"), freeze_json(ruled))
    # Neither field: the check is written under 1.3.0, and 1.4.0 refuses it.
    bare: dict[str, Any] = {
        key: value for key, value in missing_only.items() if key != "missing_for_assessment"
    }
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance("check-recorded", "1.4.0", bare)
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("check_recorded", "1.4.0"), freeze_json(bare))
