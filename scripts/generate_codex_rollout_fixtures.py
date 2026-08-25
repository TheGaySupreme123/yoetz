"""Write reviewed Codex rollout grammar fixtures and refresh derived bounds."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "tests"))

from builders.codex_rollout import (  # noqa: E402
    encode_lines,
    function_call,
    function_call_output,
    response_item,
    session_meta,
)
from yoetz.adapters.integrations.codex_capability_cells import (  # noqa: E402
    skill_manifest_capability_fields,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode  # noqa: E402

_CANARY = "sk-proj-CANARYLEGACYTOKEN0001"
_DIR = _ROOT / "fixtures" / "imports" / "codex"


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _controls() -> dict[str, Any]:
    return {
        "clock": "fixture_supplied",
        "environment": {
            "locale": "C",
            "python_hash_seeds": ["0", "1", "4294967295"],
            "timezone": "UTC",
        },
        "external_io": "forbidden",
        "ids": "fixture_supplied",
        "network": "forbidden",
        "randomness": "forbidden",
    }


def _case(
    *,
    fixture_id: str,
    purpose: str,
    variants: dict[str, bytes],
    expected: dict[str, Any],
) -> dict[str, Any]:
    input_variants: dict[str, Any] = {}
    for name, raw in variants.items():
        input_variants[name] = {
            "codex_capability_profile_id": "codex-rollout-jsonl/0.148.0/v1",
            "codex_version": "0.148.0",
            "source": {
                "byte_length": len(raw),
                "bytes_base64": _b64(raw),
                "sha256": f"sha256:{_sha(raw)}",
            },
        }
    return {
        "controls": _controls(),
        "expected": {"variants": expected},
        "fixture_id": fixture_id,
        "fixture_schema": "yoetz.fixture-case/1.0.0",
        "fixture_version": "1.0.0",
        "input": {"variants": input_variants},
        "minimum_versions": {
            "codex_cli": "0.148.0",
            "codex_mapping": "codex-rollout-jsonl/1.0.0",
            "codex_profile": "codex-rollout-jsonl/0.148.0/v1",
            "fixture_contract": "1.0.0",
            "protocol": "0.1",
        },
        "owns_requirements": [
            "ADR-005/exact-profile-admission",
            "ISSUE-418/rollout-grammar",
            "ISSUE-413/capability-cell",
        ],
        "purpose": purpose,
    }


def _legacy() -> bytes:
    return encode_lines(
        session_meta(history_mode="legacy", cwd="/canary/cwd/CANARY_LEGACY_CWD"),
        response_item(
            {
                "content": [{"text": f"CANARY_LEGACY_ASSISTANT {_CANARY}", "type": "output_text"}],
                "role": "assistant",
                "type": "message",
            }
        ),
        response_item(
            {
                "summary": [{"text": "CANARY_LEGACY_REASONING", "type": "summary_text"}],
                "type": "reasoning",
            }
        ),
        function_call(
            name="mcp__yoetz__status",
            call_id="call_yoetz_1",
            arguments='{"request_id":"CANARY_LEGACY_YOETZ_ARGS"}',
        ),
        function_call_output(call_id="call_yoetz_1", output="CANARY_LEGACY_YOETZ_OUTPUT"),
        function_call(
            name="shell",
            call_id="call_shell_1",
            arguments='{"command":"echo CANARY_LEGACY_SHELL"}',
        ),
        function_call_output(call_id="call_shell_1", output="CANARY_LEGACY_SHELL_OUTPUT"),
        {
            "payload": {"cwd": "/canary/cwd/CANARY_LEGACY_CWD", "model": "synthetic"},
            "timestamp": "2026-08-22T12:00:00.000Z",
            "type": "turn_context",
        },
        {
            "payload": {
                "message": {
                    "content": [{"text": "CANARY_LEGACY_COMPACTED", "type": "output_text"}],
                    "role": "assistant",
                    "type": "message",
                }
            },
            "timestamp": "2026-08-22T12:00:00.000Z",
            "type": "compacted",
        },
        {"payload": {}, "timestamp": "2026-08-22T12:00:00.000Z", "type": "world_state"},
    )


def _paginated() -> bytes:
    return encode_lines(
        session_meta(history_mode="paginated", ordinal=1),
        {
            "ordinal": 2,
            "payload": {
                "item": {
                    "content": [{"text": "CANARY_PAGINATED_USER", "type": "input_text"}],
                    "type": "user_message",
                },
                "type": "item_completed",
            },
            "timestamp": "2026-08-22T12:00:00.000Z",
            "type": "event_msg",
        },
        response_item(
            {
                "content": [{"text": "CANARY_PAGINATED_ASSISTANT", "type": "output_text"}],
                "role": "assistant",
                "type": "message",
            },
            ordinal=3,
        ),
        function_call(
            name="mcp__yoetz__check",
            call_id="call_yoetz_p",
            arguments="{}",
            ordinal=4,
        ),
        function_call_output(call_id="call_yoetz_p", output="ok", ordinal=5),
        function_call(name="web_search", call_id="call_web_p", arguments="{}", ordinal=6),
        function_call_output(call_id="call_web_p", output="CANARY_PAGINATED_WEB", ordinal=7),
        {
            "ordinal": 8,
            "payload": {},
            "timestamp": "2026-08-22T12:00:00.000Z",
            "type": "world_state",
        },
    )


def _write_case(name: str, document: dict[str, Any]) -> None:
    path = _DIR / name
    encoded = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"
    path.write_bytes(encoded)


def _refresh_manifest() -> None:
    manifest_path = _ROOT / "fixtures" / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    members: list[dict[str, Any]] = []
    fixture_root = _ROOT / "fixtures"
    for member in manifest["members"]:
        members.append(member)
    extra = [
        ("IMP-006", "imports/codex/rollout-legacy-0.148.0.case.json"),
        ("IMP-007", "imports/codex/rollout-paginated-0.148.0.case.json"),
        ("IMP-008", "imports/codex/rollout-truncated-0.148.0.case.json"),
        ("IMP-009", "imports/codex/rollout-zst-0.148.0.case.json"),
    ]
    by_path = {item["path"]: item for item in members}
    for fixture_id, rel in extra:
        data = (fixture_root / rel).read_bytes()
        by_path[rel] = {
            "byte_length": len(data),
            "fixture_id": fixture_id,
            "media_type": "application/vnd.yoetz.fixture-case+json",
            "path": rel,
            "sha256": _sha(data),
        }
    ordered = sorted(by_path.values(), key=lambda item: str(item["path"]).encode("ascii"))
    manifest["members"] = ordered
    manifest_path.write_bytes(json.dumps(manifest).encode("utf-8") + b"\n")


def _refresh_skill_manifest() -> None:
    path = _ROOT / "skills" / "codex" / "yoetz" / "manifest.json"
    document = json.loads(path.read_bytes())
    document.update(dict(skill_manifest_capability_fields()))
    document.pop("member_digest", None)
    document["member_digest"] = canonical_digest(document)
    path.write_bytes(canonical_encode(document) + b"\n")


def main() -> None:
    legacy = _legacy()
    paginated = _paginated()
    truncated = encode_lines(session_meta(), terminated=True) + encode_lines(
        function_call(name="shell", call_id="partial"), terminated=False
    )
    compressed = b"\x28\xb5\x2f\xfd" + b"synthetic-zstd-frame-not-decoded-in-hook-pass"
    _write_case(
        "rollout-legacy-0.148.0.case.json",
        _case(
            fixture_id="IMP-006",
            purpose=(
                "Constructed 0.148.0 legacy-history rollout grammar lock for session-stream "
                "reconciliation. Not a live isolated codex-testing capture of skill/MCP/hooks."
            ),
            variants={"legacy": legacy},
            expected={"legacy": {"history_mode": "legacy", "unknown_count": 0}},
        ),
    )
    _write_case(
        "rollout-paginated-0.148.0.case.json",
        _case(
            fixture_id="IMP-007",
            purpose=(
                "Constructed 0.148.0 paginated-history rollout grammar lock for session-stream "
                "reconciliation. Not a live isolated codex-testing capture of skill/MCP/hooks."
            ),
            variants={"paginated": paginated},
            expected={"paginated": {"history_mode": "paginated", "unknown_count": 0}},
        ),
    )
    _write_case(
        "rollout-truncated-0.148.0.case.json",
        _case(
            fixture_id="IMP-008",
            purpose="Unterminated live-append rollout tail for incremental reader hold.",
            variants={"unterminated": truncated},
            expected={"unterminated": {"unknown_count": 0}},
        ),
    )
    _write_case(
        "rollout-zst-0.148.0.case.json",
        _case(
            fixture_id="IMP-009",
            purpose=(
                "Compressed rollout (.jsonl.zst) is a distinct unsupported_format gap; "
                "the hook pass never decompresses it."
            ),
            variants={"compressed": compressed},
            expected={"compressed": {"unknown_count": 0}},
        ),
    )
    _refresh_manifest()
    _refresh_skill_manifest()


if __name__ == "__main__":
    main()
