"""Generate the owned check-conclusion vectors without modifying earlier fixture members."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from yoetz.domain.events import EventSchema, decode_payload, encode_payload
from yoetz.domain.values import freeze_json
from yoetz.protocol.canonical import canonical_digest, canonical_encode

_RELATIVE = "canonical/check-conclusion-1.3.0.case.json"
_ID = "CHK-001"


def document(root: Path) -> dict[str, Any]:
    source = json.loads((root / "fixtures/receipts/semantic-advisory.case.json").read_bytes())
    shared = source["input"]["shared_current_check"]
    provenance = source["expected"]["variants"]["success_after_durable_receipt"][
        "semantic_provenance"
    ]
    coverage = dict(shared["coverage"])
    coverage["check_types"] = ["deterministic", "semantic_model_derived"]
    coverage["ledger_freshness"] = "partial"
    coverage["known_gaps"] = ["content_unselected"]
    base: dict[str, Any] = {
        "mode": "semantic_required",
        "policies": [{"policy_id": "work-integrity", "policy_version": "0.1.0"}],
        "scope": {"claim_ids": [], "obligation_ids": []},
        "policy_executions": [
            {
                "policy_id": "work-integrity",
                "policy_version": "0.1.0",
                "outcome": "run",
                "reason": "completed",
            }
        ],
        "subject_frontier": shared["frontier"],
        "verdict": "insufficient_coverage",
        "returned_finding_ids": [],
        "suppressed_count": 0,
        "coverage": coverage,
        "semantic_status": "succeeded",
        "semantic_reason": "semantic_completed",
        "engine_version": "0.1.0",
        "projection_version": "yoetz/0.1.0",
        "semantic_provenance": provenance,
    }
    vectors: list[dict[str, Any]] = []
    for conclusion in [
        None,
        "no_material_discrepancy",
        "challenges_returned",
        "insufficient_packet",
    ]:
        version = "1.2.0" if conclusion is None else "1.3.0"
        wire = dict(base)
        if conclusion is not None:
            wire["semantic_conclusion"] = conclusion
        payload = decode_payload(EventSchema("check_recorded", version), freeze_json(wire))
        encoded = encode_payload(payload)
        vectors.append(
            {
                "schema_version": version,
                "payload": wire,
                "canonical_hex": canonical_encode(encoded).hex(),
                "digest": canonical_digest(encoded),
            }
        )
    return {
        "fixture_schema": "yoetz.fixture-case/1.0.0",
        "fixture_version": "1.0.0",
        "fixture_id": _ID,
        "purpose": "Distinguish legacy unknown review outcomes from recorded conclusions.",
        "minimum_versions": {"fixture_contract": "1.0.0", "protocol": "1.0"},
        "owns_requirements": ["ISSUE-884/check-conclusion"],
        "controls": {
            "clock": "fixture_supplied",
            "ids": "fixture_supplied",
            "network": "forbidden",
            "external_io": "forbidden",
            "randomness": "forbidden",
        },
        "input": {"vectors": vectors},
        "expected": {"legacy_conclusion": None},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("choose --write or --check")
    root = Path(__file__).resolve().parents[1]
    data = canonical_encode(document(root))
    target = root / "fixtures" / _RELATIVE
    manifest_path = root / "fixtures/manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    unrelated = [member for member in manifest["members"] if member["path"] != _RELATIVE]
    if any(member["fixture_id"] == _ID for member in unrelated):
        raise ValueError("fixture_identity_conflict")
    member = {
        "fixture_id": _ID,
        "path": _RELATIVE,
        "byte_length": len(data),
        "media_type": "application/vnd.yoetz.fixture-case+json",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    existing = [item for item in manifest["members"] if item["path"] == _RELATIVE]
    if args.check:
        if not target.is_file() or target.read_bytes() != data or existing != [member]:
            raise ValueError("check_conclusion_fixture_stale")
    else:
        manifest["members"] = sorted([*unrelated, member], key=lambda item: item["path"])
        target.write_bytes(data)
        manifest_path.write_text(json.dumps(manifest) + "\n")
    print("check conclusion fixture: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
