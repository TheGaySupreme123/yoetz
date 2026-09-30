"""Generate the owned review-dialogue vectors without modifying earlier fixture members.

Issue #905 records what the reviewer asked for on each AI-powered finding, the earlier finding a
re-raise names, and the reviewer's per-finding rulings on a check. The vectors pin the exact bytes
of the optional fields on the (unreleased) 1.3.0 events beside rows without them, so a row written
by an earlier 0.3 build keeps its recorded bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from yoetz.domain.events import EventSchema, decode_payload, encode_payload
from yoetz.domain.values import freeze_json
from yoetz.protocol.canonical import canonical_digest, canonical_encode

_RELATIVE = "canonical/review-dialogue-1.3.0.case.json"
_ID = "DLG-001"
_OBLIGATION = "obl_828ab204-0000-4000-8000-000000000001"
_FIRST = "fnd_fb333698-0000-4000-8000-000000000001"
_RESTATED = "fnd_97289e6b-0000-4000-8000-000000000002"


def _finding(
    root: Path, finding_id: str, summary: str, detail: str, refs: list[str]
) -> dict[str, Any]:
    source = json.loads((root / "fixtures/receipts/semantic-advisory.case.json").read_bytes())
    shared = source["input"]["shared_current_check"]
    provenance = source["expected"]["variants"]["success_after_durable_receipt"][
        "semantic_provenance"
    ]
    coverage = dict(shared["coverage"])
    coverage["check_types"] = ["deterministic", "semantic_model_derived"]
    return {
        "finding_id": finding_id,
        "kind": "completion_with_open_obligations",
        "origin": "semantic_model_derived",
        "priority": 1,
        "summary": summary,
        "detail": detail,
        "subject_refs": sorted(refs),
        "policy_id": "work-integrity",
        "policy_version": "0.1.0",
        "subject_frontier": shared["frontier"],
        "coverage": coverage,
        "provenance": provenance,
    }


def _check(root: Path) -> dict[str, Any]:
    source = json.loads((root / "fixtures/receipts/semantic-advisory.case.json").read_bytes())
    shared = source["input"]["shared_current_check"]
    provenance = source["expected"]["variants"]["success_after_durable_receipt"][
        "semantic_provenance"
    ]
    coverage = dict(shared["coverage"])
    coverage["check_types"] = ["deterministic", "semantic_model_derived"]
    coverage["ledger_freshness"] = "partial"
    coverage["known_gaps"] = ["semantic_packet_insufficient"]
    return {
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
        "semantic_conclusion": "no_material_discrepancy",
    }


def document(root: Path) -> dict[str, Any]:
    # numba-stencil-boundary-modes (DeepSWE v2): one environment-blocked obligation raised, then
    # restated under a new id. The earlier row has no dialogue fields; the restatement records the
    # challenge fields and names the earlier finding it restates.
    legacy = _finding(
        root,
        _FIRST,
        "Completion claim includes an open llvmlite 0.46.0 verification obligation.",
        "Please make one concrete attempt to satisfy the open obligation.",
        [_OBLIGATION],
    )
    restated = _finding(
        root,
        _RESTATED,
        "Completion claim does not satisfy the open required-version verification obligation.",
        "Make one concrete authorized attempt to obtain llvmlite 0.46.0, then run the tests.",
        [_OBLIGATION],
    )
    restated["challenge"] = {
        "alternative_interpretation": "The package index may be unreachable from this sandbox.",
        "discrepancy": "The claim is complete while the llvmlite 0.46.0 obligation is open.",
        "requested_next_step": "act",
        "uncertainty": "The recorded install failure may already be the authorized attempt.",
    }
    restated["relates_to"] = [_FIRST]
    link_only = dict(restated)
    link_only.pop("challenge")
    # kea-atomic-signal-selectors: the recheck after the repair rules the repaired finding fixed
    # (citing the regression result recorded after it) even though the packet as a whole was
    # insufficient, and cannot assess a sibling. A check without rulings keeps its earlier bytes.
    check = _check(root)
    ruled = dict(check)
    ruled["semantic_conclusion"] = "insufficient_packet"
    ruled["prior_finding_verdicts"] = [
        {"cited_refs": [], "finding_id": _RESTATED, "verdict": "unassessable"},
        {
            "cited_refs": ["res_866db2dd-0000-4000-8000-000000000003"],
            "finding_id": _FIRST,
            "verdict": "fixed",
        },
    ]
    vectors: list[dict[str, Any]] = []
    for family, version, wire in (
        ("finding_recorded", "1.3.0", legacy),
        ("finding_recorded", "1.3.0", restated),
        ("finding_recorded", "1.3.0", link_only),
        ("check_recorded", "1.3.0", check),
        ("check_recorded", "1.3.0", ruled),
    ):
        payload = decode_payload(EventSchema(family, version), freeze_json(wire))
        encoded = encode_payload(payload)
        vectors.append(
            {
                "family": family,
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
        "purpose": "Pin the additive review-dialogue event bytes beside unchanged legacy rows.",
        "minimum_versions": {"fixture_contract": "1.0.0", "protocol": "1.0"},
        "owns_requirements": ["ISSUE-905/review-dialogue"],
        "controls": {
            "clock": "fixture_supplied",
            "ids": "fixture_supplied",
            "network": "forbidden",
            "external_io": "forbidden",
            "randomness": "forbidden",
        },
        "input": {"vectors": vectors},
        "expected": {"legacy_challenge": None},
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
            raise ValueError("review_dialogue_fixture_stale")
    else:
        manifest["members"] = sorted([*unrelated, member], key=lambda item: item["path"])
        target.write_bytes(data)
        manifest_path.write_text(json.dumps(manifest) + "\n")
    print("review dialogue fixture: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
