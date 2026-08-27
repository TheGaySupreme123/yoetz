"""Exact-profile Codex rollout JSONL parser tests."""

from __future__ import annotations

import base64
import json
from typing import cast

import pytest

from builders.codex_rollout import (
    encode_lines,
    function_call,
    function_call_output,
    response_item,
    session_meta,
)
from fixture_loader import FixtureLoader, build_fixture_loader
from yoetz.adapters.importers.codex_jsonl import profile_for_codex_version
from yoetz.adapters.importers.codex_rollout_jsonl import (
    parse_codex_rollout_jsonl,
    profile_for_rollout_version,
)
from yoetz.adapters.integrations.codex_capability_cells import (
    CODEX_ROLLOUT_CLI_VERSION,
    CODEX_ROLLOUT_EVIDENCE_CASE_IDS,
    CODEX_ROLLOUT_IMPORTER_PROFILE_ID,
)
from yoetz.adapters.integrations.codex_skill import CODEX_HARNESS_PROFILE
from yoetz.ports.importer import ImportLineStatus
from yoetz.protocol.canonical import JsonValue

_CANARY_SECRET = "sk-proj-CANARYLEGACYTOKEN0001"


def _loader() -> FixtureLoader:
    return build_fixture_loader()


def test_rollout_profile_is_exact_and_distinct_from_exec() -> None:
    profile = profile_for_rollout_version("0.148.0")
    assert profile.profile_id == CODEX_ROLLOUT_IMPORTER_PROFILE_ID
    with pytest.raises(ValueError, match="unsupported_codex_profile"):
        profile_for_rollout_version("0.139.0")
    with pytest.raises(ValueError, match="unsupported_codex_profile"):
        profile_for_rollout_version("0.149.1")
    exec_profile = profile_for_codex_version("0.139.0")
    assert exec_profile.profile_id != profile.profile_id


def test_legacy_and_paginated_fixtures_map_without_unknown_lines() -> None:
    loader = _loader()
    profile = profile_for_rollout_version("0.148.0")
    for path in (
        "imports/codex/rollout-legacy-0.148.0.case.json",
        "imports/codex/rollout-paginated-0.148.0.case.json",
    ):
        case = cast(dict[str, JsonValue], loader.load_json(path))
        variants = cast(dict[str, JsonValue], cast(dict[str, JsonValue], case["input"])["variants"])
        expected_block = cast(dict[str, JsonValue], case["expected"])
        expected_variants = cast(dict[str, JsonValue], expected_block["variants"])
        for name, variant in variants.items():
            source_block = cast(dict[str, JsonValue], variant)["source"]
            raw = base64.b64decode(
                cast(str, cast(dict[str, JsonValue], source_block)["bytes_base64"]).encode("ascii"),
                validate=True,
            )
            parsed = parse_codex_rollout_jsonl(raw, profile, require_admission=True)
            unknown = sum(status is ImportLineStatus.UNKNOWN for status in parsed.statuses)
            assert unknown == 0
            assert parsed.records
            expected = cast(dict[str, JsonValue], expected_variants[name])
            assert unknown == expected["unknown_count"]
            dumped = json.dumps(
                [dict(record.value) for record in parsed.records],
                default=str,
            )
            assert _CANARY_SECRET not in dumped


def test_truncated_fixture_holds_final_line() -> None:
    loader = _loader()
    case = cast(
        dict[str, JsonValue], loader.load_json("imports/codex/rollout-truncated-0.148.0.case.json")
    )
    variants = cast(dict[str, JsonValue], cast(dict[str, JsonValue], case["input"])["variants"])
    source_block = cast(dict[str, JsonValue], variants["unterminated"])["source"]
    raw = base64.b64decode(
        cast(str, cast(dict[str, JsonValue], source_block)["bytes_base64"]).encode("ascii"),
        validate=True,
    )
    parsed = parse_codex_rollout_jsonl(
        raw, profile_for_rollout_version("0.148.0"), require_admission=True
    )
    assert "final_newline_absent" in parsed.stream_gaps
    assert parsed.lines[-1].terminated is False


def test_compressed_fixture_is_not_jsonl() -> None:
    loader = _loader()
    case = cast(
        dict[str, JsonValue], loader.load_json("imports/codex/rollout-zst-0.148.0.case.json")
    )
    variants = cast(dict[str, JsonValue], cast(dict[str, JsonValue], case["input"])["variants"])
    source_block = cast(dict[str, JsonValue], variants["compressed"])["source"]
    raw = base64.b64decode(
        cast(str, cast(dict[str, JsonValue], source_block)["bytes_base64"]).encode("ascii"),
        validate=True,
    )
    assert raw.startswith(b"\x28\xb5\x2f\xfd")
    parsed = parse_codex_rollout_jsonl(raw, profile_for_rollout_version("0.148.0"))
    assert parsed.records == ()
    assert all(status is ImportLineStatus.MALFORMED for status in parsed.statuses)


def test_redaction_canary_is_stripped_from_records() -> None:
    profile = profile_for_rollout_version("0.148.0")
    source = encode_lines(
        session_meta(),
        response_item(
            {
                "content": [{"text": f"token {_CANARY_SECRET}", "type": "output_text"}],
                "role": "assistant",
                "type": "message",
            }
        ),
        function_call(
            name="mcp__yoetz__status",
            call_id="call_yoetz_1",
            arguments='{"request_id":"req_canary"}',
        ),
        function_call_output(call_id="call_yoetz_1", output="ok"),
    )
    parsed = parse_codex_rollout_jsonl(source, profile, require_admission=True)
    assert all(status is not ImportLineStatus.UNKNOWN for status in parsed.statuses)
    tools = [record.item_type for record in parsed.records]
    assert "function_call" in tools
    assert _CANARY_SECRET not in repr(parsed.records)


def test_wrong_cli_version_is_unsupported_format() -> None:
    profile = profile_for_rollout_version("0.148.0")
    source = encode_lines(
        session_meta(cli_version="0.149.1"),
        function_call(name="shell", call_id="x"),
    )
    parsed = parse_codex_rollout_jsonl(source, profile, require_admission=True)
    assert parsed.records == ()
    assert parsed.stream_gaps == ("unsupported_codex_profile",)
    assert all(status is ImportLineStatus.UNSUPPORTED for status in parsed.statuses)


@pytest.mark.parametrize("history_mode", ["future", None, 1])
def test_unknown_or_invalid_history_mode_is_unsupported_format(history_mode: object) -> None:
    profile = profile_for_rollout_version("0.148.0")
    meta = session_meta()
    payload = cast(dict[str, object], meta["payload"])
    if history_mode is None:
        payload.pop("history_mode")
    else:
        payload["history_mode"] = history_mode
    source = encode_lines(meta, function_call(name="shell", call_id="x"))

    parsed = parse_codex_rollout_jsonl(source, profile, require_admission=True)

    assert parsed.records == ()
    assert parsed.stream_gaps == ("unsupported_codex_profile",)
    assert all(status is ImportLineStatus.UNSUPPORTED for status in parsed.statuses)


def test_unknown_item_type_is_not_mapped_as_clean_coverage() -> None:
    parsed = parse_codex_rollout_jsonl(
        encode_lines(session_meta(), response_item({"type": "future_tool_result"})),
        profile_for_rollout_version("0.148.0"),
        require_admission=True,
    )
    assert ImportLineStatus.UNKNOWN in parsed.statuses
    assert "unknown_item_type" in parsed.reason_codes
    assert all(record.item_type != "future_tool_result" for record in parsed.records)


def test_exec_jsonl_is_unsupported_codex_profile() -> None:
    profile = profile_for_rollout_version("0.148.0")
    source = (
        b'{"type":"item.completed","item":{"id":"i1","type":"command_execution",'
        b'"command":"echo","aggregated_output":"ok","exit_code":0,"status":"completed"}}\n'
    )
    parsed = parse_codex_rollout_jsonl(source, profile, require_admission=True)
    assert parsed.records == ()
    assert "unsupported_codex_profile" in parsed.stream_gaps
    assert all(status is ImportLineStatus.UNSUPPORTED for status in parsed.statuses)


def test_rollout_fixture_cell_does_not_promote_harness_support() -> None:
    assert CODEX_HARNESS_PROFILE.capability_profile_ids == ()
    assert CODEX_HARNESS_PROFILE.supported_versions == ()
    assert dict(CODEX_HARNESS_PROFILE.hooks_by_capability_profile) == {}
    assert CODEX_ROLLOUT_CLI_VERSION == "0.148.0"
    assert CODEX_ROLLOUT_EVIDENCE_CASE_IDS == ("IMP-006", "IMP-007", "IMP-008", "IMP-009")
