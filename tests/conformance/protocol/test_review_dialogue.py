"""Review-dialogue event vectors keep legacy bytes and fence the new fields (issue #905)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from yoetz.domain.events import EventSchema, decode_payload, encode_payload, finding_event_schema
from yoetz.domain.findings import Finding, FindingChallenge, finding_to_json
from yoetz.domain.values import freeze_json
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.schemas import SchemaInstanceInvalid, validate_schema_instance


def _vectors() -> list[dict[str, Any]]:
    path = (
        Path(__file__).resolve().parents[3] / "fixtures/canonical/review-dialogue-1.4.0.case.json"
    )
    return json.loads(path.read_bytes())["input"]["vectors"]


def _schema_rejects(version: str, wire: dict[str, Any]) -> None:
    with pytest.raises(SchemaInstanceInvalid):
        validate_schema_instance("finding-recorded", version, wire)


def test_golden_findings_round_trip_through_schema_and_domain() -> None:
    for vector in _vectors():
        version, wire = vector["schema_version"], vector["payload"]
        validate_schema_instance("finding-recorded", version, wire)
        payload = decode_payload(EventSchema("finding_recorded", version), freeze_json(wire))
        assert type(payload) is Finding
        # The writer picks exactly the version the vector was recorded under.
        assert finding_event_schema(payload) == EventSchema("finding_recorded", version)
        encoded = encode_payload(payload)
        assert canonical_encode(encoded).hex() == vector["canonical_hex"]
        assert canonical_digest(encoded) == vector["digest"]
        # The public finding wire never carries the event-only dialogue fields.
        public = finding_to_json(payload)
        assert "challenge" not in public
        assert "relates_to" not in public


def test_dialogue_fields_are_admitted_only_on_their_version() -> None:
    legacy, restated, _link_only = _vectors()
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("finding_recorded", "1.3.0"), freeze_json(restated["payload"]))
    _schema_rejects("1.3.0", restated["payload"])
    # 1.4.0 is written only when a dialogue field is present, so a bare row never reads as one.
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("finding_recorded", "1.4.0"), freeze_json(legacy["payload"]))
    _schema_rejects("1.4.0", legacy["payload"])


def test_local_findings_and_malformed_dialogue_fields_are_refused() -> None:
    restated = _vectors()[1]["payload"]
    local = {**restated, "origin": "deterministic"}
    local.pop("provenance")
    with pytest.raises(ProtocolValueError):
        decode_payload(EventSchema("finding_recorded", "1.4.0"), freeze_json(local))
    _schema_rejects("1.4.0", local)

    malformed: list[dict[str, Any]] = [
        {**restated, "relates_to": [restated["finding_id"]]},
        {**restated, "relates_to": []},
        {**restated, "challenge": {**restated["challenge"], "requested_next_step": "wait"}},
        {**restated, "challenge": {**restated["challenge"], "discrepancy": ""}},
        {**restated, "challenge": {**restated["challenge"], "extra": "x"}},
    ]
    for wire in malformed:
        with pytest.raises(ProtocolValueError):
            decode_payload(EventSchema("finding_recorded", "1.4.0"), freeze_json(wire))
    oversized = {**restated["challenge"], "uncertainty": "é" * 2049}
    with pytest.raises(ProtocolValueError):
        FindingChallenge(
            oversized["discrepancy"],
            oversized["alternative_interpretation"],
            oversized["requested_next_step"],
            oversized["uncertainty"],
        )


def test_outbound_case_names_the_prior_findings_section_only_from_1_2_0() -> None:
    root = Path(__file__).resolve().parents[3] / "schemas/privacy"
    before = json.loads((root / "outbound-case-1.1.0.schema.json").read_bytes())
    after = json.loads((root / "outbound-case-1.2.0.schema.json").read_bytes())
    assert "prior_finding" not in before["$defs"]["content_item"]["properties"]["section"]["enum"]
    assert "prior_finding" in after["$defs"]["content_item"]["properties"]["section"]["enum"]
    assert "prior_finding_item_ids" not in before["$defs"]["review_packet"]["properties"]
    assert "prior_finding_item_ids" in after["$defs"]["review_packet"]["required"]
    # No new data category crosses egress with the section.
    assert before["$defs"]["data_category"] == after["$defs"]["data_category"]
