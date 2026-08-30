"""Manifest-bound wire vector for append-only claim correction."""

from __future__ import annotations

from typing import Any, cast

from fixture_loader import load_fixture_json
from yoetz.domain.events import ClaimRecordedPayloadV1_1, EventSchema, decode_payload
from yoetz.domain.values import freeze_json
from yoetz.protocol.schemas import validate_schema_instance


def test_claim_correction_fixture_freezes_canonical_replacement_shape() -> None:
    document = cast(dict[str, Any], load_fixture_json("canonical/claim-correction-1.1.0.case.json"))
    replacement = cast(dict[str, Any], cast(dict[str, Any], document["input"])["replacement_claim"])
    schema_wire = cast(dict[str, str], replacement["schema"])
    payload_wire = freeze_json(replacement["payload"])

    assert schema_wire == {"name": "claim_recorded", "version": "1.1.0"}
    validate_schema_instance("claim-recorded", "1.1.0", payload_wire)
    payload = decode_payload(EventSchema("claim_recorded", "1.1.0"), payload_wire)
    assert type(payload) is ClaimRecordedPayloadV1_1

    expected = cast(dict[str, Any], document["expected"])
    assert list(payload.supersedes_claim_refs) == expected["historical_claim_ids"][:1]
    assert list(payload.limitation_refs) == [expected["status_result"]["result_id"]]
    assert expected["effective_claim_ids"] == [payload.claim_id]
    assert expected["invalid_replacements"] == [
        {"field": "limitation_refs", "invariant": "limitation_refs_complete"},
        {"field": "disputes_refs", "invariant": "replacement_must_not_dispute"},
        {"field": "supersedes_claim_refs", "invariant": "superseded_claim_must_be_effective"},
    ]
