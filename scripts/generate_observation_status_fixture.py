"""Write the canonical 2.6 observation-status selection wire fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from yoetz.protocol.canonical import canonical_digest, canonical_encode, strict_json_parse

_FIXTURE_RELATIVE = Path("fixtures/canonical/control-observation-status-2.6.case.json")
_FIXTURE_MANIFEST_PATH = "canonical/control-observation-status-2.6.case.json"
_MANIFEST_RELATIVE = Path("fixtures/manifest.json")
_FIXTURE_ID = "CTL-260"
_FIXTURE_MEDIA_TYPE = "application/vnd.yoetz.fixture-case+json"
_INSTANCE_ID = "svc_00000000-0000-4000-8000-000000000001"
_RPC_ID = "rpc_00000000-0000-4000-8000-000000000002"
_REQUEST_ID = "req_00000000-0000-4000-8000-000000000003"
_COMMITMENT = "hmac-sha256:" + "1" * 64
_DIGEST = "sha256:" + "2" * 64


def _runtime_status() -> dict[str, Any]:
    return {
        "selected_mode": "detailed",
        "effective_mode": "focused",
        "selected_capacity": 2048,
        "effective_capacity": 2048,
        "selection_origin": "session",
        "selection_expires_at": "2026-09-10T01:00:00.000Z",
        "pressure_state": "rising",
        "pressure_transition_identity": _DIGEST,
        "content_allowed": True,
        "admission_allowed": True,
        "queue_count": 2,
        "queue_bytes": 1024,
        "state_bytes": 4096,
        "oldest_pending_age_ms": 250,
        "pending_attempts": 1,
        "pending_lifecycle_count": 0,
        "capture_backlog": {
            "capture_backlog_scope": "partial",
            "route_count": 1,
            "count": 1,
            "byte_count": 512,
            "oldest_receipt_time": "2026-09-10T00:00:00.000Z",
            "observed_at": "2026-09-10T00:00:01.000Z",
            "reservation_count": 0,
            "reserved_byte_count": 0,
            "reservation_unknown": False,
            "routes": {
                "tsk_00000000-0000-4000-8000-000000000004": {
                    "count": 1,
                    "byte_count": 512,
                    "oldest_receipt_time": "2026-09-10T00:00:00.000Z",
                    "observed_at": "2026-09-10T00:00:01.000Z",
                    "reservation_count": 0,
                    "reservation_unknown": False,
                }
            },
        },
        "protected_count_reserve": 512,
        "protected_bytes_reserve": 131072,
        "session_fair_share": 512,
        "session_fair_share_bytes": 524288,
        "accounting": {
            "observed_count": 4,
            "accounting_scope": "locally_ingested_since_selection_upgrade",
            "admitted_input_count": 3,
            "delivered_input_count": 2,
            "summarized_input_count": 1,
            "intentionally_omitted_input_count": 1,
            "check_selection": "reported_by_each_check",
            "summary_record_count": 1,
            "buffered_input_count": 1,
            "pending_attempt_count": 1,
            "buffered_successful_call_count": 1,
            "unrecoverable_input_count": 0,
            "loss_identity_commitment": None,
            "loss_ranges": [],
            "loss_identity_list_complete": False,
            "selection_epoch": 3,
        },
        "session_commitment": _COMMITMENT,
        "policy_version": "observation-budget-v1-provisional",
        "validation_status": "not_validated",
    }


def _frame() -> dict[str, Any]:
    return {
        "body": {
            "request_id": _REQUEST_ID,
            "schema_version": "1.0.0",
            "status": {
                "lifecycle": "active",
                "workspace_commitment": _COMMITMENT,
                "source_coverage": {
                    "claude_hook": True,
                    "codex_hook": True,
                    "codex_session_stream": False,
                    "cursor_hook": False,
                },
                "last_observation_receipt_time": "2026-09-10T00:00:01.000Z",
                "lag_events": 0,
                "gaps": [],
                "unsupported_events": [],
                "advice_frontier": None,
                "selection_runtime": _runtime_status(),
            },
        },
        "method": "observation_status",
        "outcome": "ok",
        "protocol_version": "1.0",
        "rpc_id": _RPC_ID,
        "service_generation": "1",
        "service_instance_id": _INSTANCE_ID,
    }


def _fixture_document() -> dict[str, Any]:
    frame = _frame()
    encoded = canonical_encode(frame)
    return {
        "controls": {
            "clock": "fixture_supplied",
            "external_io": "forbidden",
            "network": "forbidden",
            "randomness": "fixture_supplied",
        },
        "expected": {
            "canonical_byte_length": len(encoded),
            "canonical_hex": encoded.hex(),
            "canonical_sha256": canonical_digest(frame),
            "new_schema_version": "2.6.0",
            "old_schema_version": "2.5.0",
            "selection_runtime_present": True,
        },
        "fixture_id": _FIXTURE_ID,
        "fixture_schema": "yoetz.fixture-case/1.0.0",
        "fixture_version": "1.0.0",
        "input": {"frame": frame},
        "minimum_versions": {
            "control_schema": "2.6.0",
            "fixture_contract": "1.0.0",
            "protocol": "1.0",
        },
        "owns_requirements": ["ISSUE-687:selection-runtime-status", "ADR-029"],
        "purpose": (
            "Freeze the bounded structural selection-runtime projection on the additive 2.6 "
            "observation-status result wire while proving the frozen 2.5 wire rejects it."
        ),
    }


def _manifest_bytes(path: Path, fixture_bytes: bytes, *, allow_owned_update: bool) -> bytes:
    parsed = strict_json_parse(path.read_bytes())
    if not isinstance(parsed, dict) or set(parsed) != {
        "manifest_schema",
        "manifest_version",
        "members",
    }:
        raise ValueError("fixture_manifest_shape_invalid")
    members = parsed["members"]
    if not isinstance(members, list):
        raise ValueError("fixture_manifest_members_invalid")
    previous = [cast(dict[str, Any], item) for item in members]
    if previous != sorted(previous, key=lambda item: str(item["path"]).encode("ascii")):
        raise ValueError("fixture_manifest_order_invalid")
    member = {
        "byte_length": len(fixture_bytes),
        "fixture_id": _FIXTURE_ID,
        "media_type": _FIXTURE_MEDIA_TYPE,
        "path": _FIXTURE_MANIFEST_PATH,
        "sha256": hashlib.sha256(fixture_bytes).hexdigest(),
    }
    existing = [item for item in previous if item["path"] == _FIXTURE_MANIFEST_PATH]
    if len(existing) > 1:
        raise ValueError("fixture_manifest_duplicate_path")
    if existing and existing[0] != member and not allow_owned_update:
        raise ValueError("fixture_manifest_owned_member_changed")
    if any(
        item["fixture_id"] == _FIXTURE_ID and item["path"] != _FIXTURE_MANIFEST_PATH
        for item in previous
    ):
        raise ValueError("fixture_manifest_id_already_owned")
    updated_members = (
        [member if item["path"] == _FIXTURE_MANIFEST_PATH else item for item in previous]
        if existing
        else sorted([*previous, member], key=lambda item: str(item["path"]).encode("ascii"))
    )
    if [item for item in updated_members if item["path"] != _FIXTURE_MANIFEST_PATH] != [
        item for item in previous if item["path"] != _FIXTURE_MANIFEST_PATH
    ]:
        raise ValueError("fixture_manifest_existing_member_changed")
    updated = cast(dict[str, Any], dict(parsed))
    updated["members"] = updated_members
    return json.dumps(updated).encode("utf-8") + b"\n"


def _expected(root: Path, *, allow_owned_update: bool) -> tuple[bytes, bytes]:
    fixture_bytes = canonical_encode(_fixture_document())
    manifest_bytes = _manifest_bytes(
        root / _MANIFEST_RELATIVE,
        fixture_bytes,
        allow_owned_update=allow_owned_update,
    )
    return fixture_bytes, manifest_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.check == args.write:
        parser.error("choose exactly one of --check or --write")
    root = args.repo_root.resolve()
    fixture_bytes, manifest_bytes = _expected(root, allow_owned_update=args.write)
    fixture_path = root / _FIXTURE_RELATIVE
    manifest_path = root / _MANIFEST_RELATIVE
    if args.check:
        if not fixture_path.is_file() or fixture_path.read_bytes() != fixture_bytes:
            print("observation status fixture stale")
            return 1
        if not manifest_path.is_file() or manifest_path.read_bytes() != manifest_bytes:
            print("fixture manifest stale")
            return 1
        print("observation status fixture and manifest are current")
        return 0
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(fixture_bytes)
    manifest_path.write_bytes(manifest_bytes)
    print("wrote CTL-260 observation status fixture and manifest member")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
