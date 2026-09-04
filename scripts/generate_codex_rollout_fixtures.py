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
    event_msg,
    function_call,
    function_call_output,
    item_completed,
    response_item,
    session_meta,
)
from yoetz.adapters.importers.codex_rollout_jsonl import (  # noqa: E402
    SUPPORTED_ROLLOUT_PROFILES,
)
from yoetz.adapters.integrations.codex_capability_cells import (  # noqa: E402
    skill_manifest_capability_fields,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode  # noqa: E402

_CANARY = "sk-proj-CANARYLEGACYTOKEN0001"
_DIR = _ROOT / "fixtures" / "imports" / "codex"
_TS = "2026-08-22T12:00:00.000Z"
_WRAPPER_TYPES_0_150_1 = SUPPORTED_ROLLOUT_PROFILES["0.150.1"].wrapper_types
_ITEM_TYPES_0_150_1 = SUPPORTED_ROLLOUT_PROFILES["0.150.1"].item_types
# Every fixture is constructed from the observed key shape of a release, never copied from a
# real rollout: values are CANARY tokens so tests can prove nothing content-bearing leaks.
_VERSIONS = {
    "0.148.0": "codex-rollout-jsonl/0.148.0/v1",
    "0.150.1": "codex-rollout-jsonl/0.150.1/v1",
}


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
    cli_version: str = "0.148.0",
    profile_id: str | None = None,
    requirements: tuple[str, ...] = (
        "ADR-005/exact-profile-admission",
        "ISSUE-418/rollout-grammar",
        "ISSUE-413/capability-cell",
    ),
) -> dict[str, Any]:
    # ``profile_id`` names the exact profile expected to admit the source; an unsupported
    # release has none, and its case records the refusal instead.
    resolved_profile = _VERSIONS[cli_version] if profile_id is None else profile_id
    input_variants: dict[str, Any] = {}
    for name, raw in variants.items():
        input_variants[name] = {
            "codex_capability_profile_id": resolved_profile,
            "codex_version": cli_version,
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
            "codex_cli": cli_version,
            "codex_mapping": "codex-rollout-jsonl/1.0.0",
            "codex_profile": resolved_profile,
            "fixture_contract": "1.0.0",
            "protocol": "0.1",
        },
        "owns_requirements": list(requirements),
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


def _current_0_150_1() -> bytes:
    """Constructed paginated (current-mode) 0.150.1 rollout covering every admitted shape.

    Wrapper and item key sets mirror the observed 0.150.1 grammar; every value is a canary.
    Hidden reasoning (``encrypted_content``, ``raw_content``), the system prompt
    (``base_instructions``), developer messages, and a secret-looking token are all present so
    the privacy tests can prove none of them reach an observation envelope.
    """

    ordinal = 0

    def next_ordinal() -> int:
        nonlocal ordinal
        ordinal += 1
        return ordinal

    return encode_lines(
        session_meta(
            cli_version="0.150.1",
            history_mode="paginated",
            ordinal=next_ordinal(),
            cwd="/canary/cwd/CANARY_0150_CWD",
            extra={
                "base_instructions": {"text": "CANARY_0150_SYSTEM_PROMPT"},
                "context_window": 1,
                "model_provider": "synthetic",
                "session_id": "019f8b27-b98e-7061-bbb5-d0b897594de6",
                "source": "cli",
                "thread_source": "cli",
                "timestamp": _TS,
            },
        ),
        {
            "ordinal": next_ordinal(),
            "payload": {
                "active_permission_profile": "CANARY_0150_PERMISSION_PROFILE",
                "approval_policy": "never",
                "cwd": "/canary/cwd/CANARY_0150_CWD",
                "model": "synthetic",
                "turn_id": "turn_1",
            },
            "timestamp": _TS,
            "type": "turn_context",
        },
        event_msg(
            {"thread_settings": {"model": "synthetic"}, "type": "thread_settings_applied"},
            ordinal=next_ordinal(),
        ),
        event_msg(
            {"started_at": _TS, "turn_id": "turn_1", "type": "task_started"},
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "content": [{"text": "CANARY_0150_DEVELOPER_PROMPT", "type": "input_text"}],
                "id": "msg_dev_1",
                "role": "developer",
                "type": "message",
            },
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "content": [{"text": "CANARY_0150_USER_TEXT", "type": "input_text"}],
                "id": "msg_user_1",
                "role": "user",
                "type": "message",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "content": [{"text": "CANARY_0150_USER_TEXT", "type": "input_text"}],
                "id": "item_user_1",
                "type": "UserMessage",
            },
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "encrypted_content": "CANARY_0150_HIDDEN_REASONING",
                "id": "rs_1",
                "summary": [{"text": "CANARY_0150_REASONING_SUMMARY", "type": "summary_text"}],
                "type": "reasoning",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "id": "item_reasoning_1",
                "raw_content": "CANARY_0150_HIDDEN_REASONING",
                "summary_text": "CANARY_0150_REASONING_SUMMARY",
                "type": "Reasoning",
            },
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "arguments": '{"request_id":"CANARY_0150_YOETZ_ARGS"}',
                "call_id": "call_yoetz_1",
                "id": "fc_1",
                "name": "mcp__yoetz__status",
                "namespace": "yoetz",
                "type": "function_call",
            },
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "call_id": "call_yoetz_1",
                "id": "fco_1",
                "output": "CANARY_0150_YOETZ_OUTPUT",
                "type": "function_call_output",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "arguments": {"request_id": "CANARY_0150_YOETZ_ARGS"},
                "duration": {"nanos": 0, "secs": 1},
                "id": "call_yoetz_1",
                "result": {"content": [{"text": "CANARY_0150_YOETZ_OUTPUT", "type": "text"}]},
                "server": "yoetz",
                "status": "completed",
                "tool": "status",
                "type": "McpToolCall",
            },
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "call_id": "call_shell_1",
                "id": "ctc_1",
                "input": f"echo CANARY_0150_SHELL api_key={_CANARY}",
                "name": "shell_command",
                "status": "completed",
                "type": "custom_tool_call",
            },
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "call_id": "call_shell_1",
                "id": "ctco_1",
                "output": f"CANARY_0150_SHELL_OUTPUT {_CANARY}",
                "type": "custom_tool_call_output",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "aggregated_output": "CANARY_0150_SHELL_OUTPUT",
                "command": "echo CANARY_0150_SHELL",
                "cwd": "/canary/cwd/CANARY_0150_CWD",
                "duration": {"nanos": 0, "secs": 1},
                "exit_code": 0,
                "id": "call_shell_1",
                "parsed_cmd": [],
                "source": "agent",
                "status": "completed",
                "stderr": "",
                "stdout": "CANARY_0150_SHELL_OUTPUT",
                "type": "CommandExecution",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "changes": [{"kind": "update", "path": "/canary/cwd/CANARY_0150_FILE"}],
                "id": "item_file_1",
                "status": "completed",
                "stderr": "",
                "stdout": "",
                "type": "FileChange",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "agents_states": {},
                "id": "item_collab_1",
                "receiver_agents": [],
                "receiver_thread_ids": [],
                "sender_thread_id": "019f8b27-b98e-7061-bbb5-d0b897594de6",
                "status": "completed",
                "tool": "spawn_agent",
                "type": "CollabAgentToolCall",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "agent_path": "CANARY_0150_AGENT_PATH",
                "agent_thread_id": "019f8b27-b98e-7061-bbb5-d0b897594de7",
                "id": "item_subagent_1",
                "kind": "started",
                "type": "SubAgentActivity",
            },
            ordinal=next_ordinal(),
        ),
        {
            "ordinal": next_ordinal(),
            "payload": {"trigger_turn": "turn_1"},
            "timestamp": _TS,
            "type": "inter_agent_communication_metadata",
        },
        response_item(
            {
                "author": "CANARY_0150_AGENT_AUTHOR",
                "content": "CANARY_0150_AGENT_MESSAGE",
                "id": "am_1",
                "recipient": "CANARY_0150_AGENT_RECIPIENT",
                "type": "agent_message",
            },
            ordinal=next_ordinal(),
        ),
        response_item(
            {
                "content": [{"text": "CANARY_0150_ASSISTANT_TEXT", "type": "output_text"}],
                "id": "msg_assistant_1",
                "phase": "final_answer",
                "role": "assistant",
                "type": "message",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {
                "content": "CANARY_0150_ASSISTANT_TEXT",
                "id": "item_assistant_1",
                "phase": "final_answer",
                "type": "AgentMessage",
            },
            ordinal=next_ordinal(),
        ),
        event_msg(
            {
                "info": {"total_token_usage": {"input_tokens": 1}},
                "rate_limits": {},
                "type": "token_count",
            },
            ordinal=next_ordinal(),
        ),
        event_msg(
            {
                "completed_at": _TS,
                "duration_ms": 1,
                "last_agent_message": "CANARY_0150_ASSISTANT_TEXT",
                "started_at": _TS,
                "turn_id": "turn_1",
                "type": "task_complete",
            },
            ordinal=next_ordinal(),
        ),
        event_msg(
            {"started_at": _TS, "turn_id": "turn_2", "type": "task_started"},
            ordinal=next_ordinal(),
        ),
        event_msg(
            {
                "completed_at": _TS,
                "duration_ms": 1,
                "reason": "interrupted",
                "started_at": _TS,
                "turn_id": "turn_2",
                "type": "turn_aborted",
            },
            ordinal=next_ordinal(),
        ),
        item_completed(
            {"id": "item_compaction_1", "type": "ContextCompaction"},
            ordinal=next_ordinal(),
        ),
        {
            "ordinal": next_ordinal(),
            "payload": {
                "first_window_id": "w1",
                "message": "CANARY_0150_COMPACTED_SUMMARY",
                "previous_window_id": "w1",
                "replacement_history": [],
                "window_id": "w2",
                "window_number": 2,
            },
            "timestamp": _TS,
            "type": "compacted",
        },
        {
            "ordinal": next_ordinal(),
            "payload": {"full": True, "state": {"files": ["CANARY_0150_WORLD_FILE"]}},
            "timestamp": _TS,
            "type": "world_state",
        },
    )


def _unsupported_0_152_1() -> bytes:
    """A newer release's header plus one wrapper the supported grammars do not name."""

    return encode_lines(
        session_meta(cli_version="0.152.1", history_mode="paginated", ordinal=1),
        {
            "ordinal": 2,
            "payload": {"tokens": 1},
            "timestamp": _TS,
            "type": "token_usage_record",
        },
        function_call(name="shell", call_id="call_future_1", ordinal=3),
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
        ("IMP-011", "imports/codex/rollout-paginated-0.150.1.case.json"),
        ("IMP-012", "imports/codex/rollout-truncated-0.150.1.case.json"),
        ("IMP-013", "imports/codex/rollout-unsupported-0.152.1.case.json"),
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
    current = _current_0_150_1()
    truncated_current = encode_lines(
        session_meta(cli_version="0.150.1", history_mode="paginated", ordinal=1),
        terminated=True,
    ) + encode_lines(
        item_completed({"id": "item_partial", "type": "AgentMessage"}, ordinal=2),
        terminated=False,
    )
    _write_case(
        "rollout-paginated-0.150.1.case.json",
        _case(
            fixture_id="IMP-011",
            cli_version="0.150.1",
            purpose=(
                "Constructed 0.150.1 paginated (current-mode) rollout grammar lock: every "
                "wrapper and item type the 0.150.1 profile admits appears exactly once or more, "
                "with hidden reasoning, system/developer prompts, and a secret canary present "
                "so privacy tests prove none reach observation envelopes. Not a live isolated "
                "codex-testing capture of skill/MCP/hooks; only paginated history was observed."
            ),
            variants={"paginated": current},
            expected={
                "paginated": {
                    "history_mode": "paginated",
                    "item_types": sorted(_ITEM_TYPES_0_150_1, key=str.encode),
                    "unknown_count": 0,
                    "wrapper_types": sorted(_WRAPPER_TYPES_0_150_1, key=str.encode),
                }
            },
            requirements=(
                "ADR-005/exact-profile-admission",
                "ISSUE-568/rollout-grammar-0.150.1",
            ),
        ),
    )
    _write_case(
        "rollout-truncated-0.150.1.case.json",
        _case(
            fixture_id="IMP-012",
            cli_version="0.150.1",
            purpose="Unterminated live-append 0.150.1 rollout tail for incremental reader hold.",
            variants={"unterminated": truncated_current},
            expected={"unterminated": {"unknown_count": 0}},
            requirements=(
                "ADR-005/exact-profile-admission",
                "ISSUE-568/rollout-grammar-0.150.1",
            ),
        ),
    )
    _write_case(
        "rollout-unsupported-0.152.1.case.json",
        _case(
            fixture_id="IMP-013",
            cli_version="0.152.1",
            profile_id="unsupported",
            purpose=(
                "A release with no exact profile is refused at the session header as "
                "unsupported_codex_profile; no line maps under a neighbouring profile and the "
                "stream reader keeps a durable refused cursor instead of losing position."
            ),
            variants={"future": _unsupported_0_152_1()},
            expected={
                "future": {
                    "admitted": False,
                    "stream_gaps": ["unsupported_codex_profile"],
                    "unknown_count": 0,
                }
            },
            requirements=(
                "ADR-005/exact-profile-admission",
                "ISSUE-568/unsupported-release-bounded-gap",
            ),
        ),
    )
    _refresh_manifest()
    _refresh_skill_manifest()


if __name__ == "__main__":
    main()
