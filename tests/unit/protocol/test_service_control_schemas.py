"""Frozen local-service control schema matrix and trust-boundary checks."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from importlib import resources
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import pytest

from yoetz.protocol.canonical import JsonValue, strict_json_parse
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.schemas import load_schema_catalog, validate_schema_instance

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ROOT = _REPO_ROOT / "schemas" / "service"
_PACKAGE_ROOT = resources.files("yoetz").joinpath("resources", "schemas", "service")
_VERSION = "1.0.0"
_INSTANCE_ID = "svc_00000000-0000-4000-8000-000000000001"
_RPC_ID = "rpc_00000000-0000-4000-8000-000000000002"
_REQUEST_ID = "req_00000000-0000-4000-8000-000000000003"
_DIGEST = "sha256:" + "0" * 64
_WORKFLOW_METHODS = (
    "check",
    "publish_work",
    "receipt",
    "respond",
    "start",
    "status",
)
_SUPPORT_METHODS = (
    "backup_execute",
    "backup_preview",
    "import_codex_jsonl",
    "integration_execute",
    "integration_preview",
    "migrate_execute",
    "migrate_preview",
    "observation_ingest",
    "observation_pause",
    "observation_resume",
    "observation_revoke",
    "observation_status",
    "privacy_get_effective",
    "privacy_get_setup",
    "privacy_pending_list",
    "privacy_propose_policy",
    "privacy_receipts_get",
    "privacy_receipts_list",
    "privacy_tighten_policy",
    "restore_execute",
    "restore_preview",
    "review",
    "service_lock",
    "service_status",
    "service_stop",
)
_ALL_METHODS = tuple(sorted((*_WORKFLOW_METHODS, *_SUPPORT_METHODS)))
_CONTROL_ERROR_CODES = (
    "protocol_mismatch",
    "frame_invalid",
    "frame_too_large",
    "request_cancelled",
    "request_timeout",
    "vault_locked",
    "service_draining",
    "method_forbidden",
    "service_generation_changed",
    "internal_error",
)


def _schema(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any], strict_json_parse((_ROOT / f"{name}-{_VERSION}.schema.json").read_bytes())
    )


def _valid_status(*, ready: bool = False) -> dict[str, JsonValue]:
    if ready:
        return {
            "protocol_version": "1.0",
            "service_version": "0.1.0",
            "service_instance_id": _INSTANCE_ID,
            "service_generation": "1",
            "state": "ready",
            "state_reason": "human_authority_unavailable",
            "vault_mode": "os_keyring",
            "capabilities": [
                "confidential_ingress",
                "import_review",
                "maintenance",
                "session_event_monitor",
                "workflow",
            ],
            "session_monitor": "active",
            "idle_relock_seconds": 60,
        }
    return {
        "protocol_version": "1.0",
        "service_version": "0.1.0",
        "service_instance_id": _INSTANCE_ID,
        "service_generation": "1",
        "state": "locked",
        "state_reason": "human_authority_unavailable",
        "vault_mode": "uninitialized",
        "capabilities": [],
        "session_monitor": "unavailable",
    }


def _assert_invalid(name: str, value: JsonValue) -> None:
    with pytest.raises(ProtocolValueError) as caught:
        validate_schema_instance(name, _VERSION, value)
    assert caught.value.reason_code == "schema_instance_invalid"


def _walk(node: object) -> Iterator[tuple[str, object]]:
    if isinstance(node, dict):
        for key, value in cast(dict[str, object], node).items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for value in cast(list[object], node):
            yield from _walk(value)


def test_five_service_schemas_are_root_package_identical_and_offline() -> None:
    names = (
        "control-hello",
        "control-hello-result",
        "control-request",
        "control-result",
        "service-status",
    )
    known_ids = set(load_schema_catalog().by_id)
    for name in names:
        filename = f"{name}-{_VERSION}.schema.json"
        root_bytes = (_ROOT / filename).read_bytes()
        assert root_bytes == _PACKAGE_ROOT.joinpath(filename).read_bytes()
        document = _schema(name)
        for key, value in _walk(document):
            if key != "$ref":
                continue
            ref = cast(str, value)
            parsed = urlparse(ref)
            if not parsed.scheme:
                assert ref.startswith("#/")
            else:
                assert parsed.scheme == "https"
                assert parsed.netloc == "schemas.yoetz.dev"
                assert ref.split("#", 1)[0] in known_ids


def test_hello_status_and_client_kind_authority_matrix() -> None:
    hello: dict[str, JsonValue] = {
        "protocol_version": "1.0",
        "client_kind": "mcp_bridge",
        "client_version": "0.1.0",
        "connection_nonce": "0" * 64,
        "schema_manifest_digest": _DIGEST,
    }
    validate_schema_instance("control-hello", _VERSION, hello)
    for invalid_nonce in ("0" * 63, "A" * 64, "g" * 64):
        candidate = dict(hello)
        candidate["connection_nonce"] = invalid_nonce
        _assert_invalid("control-hello", candidate)

    locked = _valid_status()
    ready_local = _valid_status(ready=True)
    validate_schema_instance("service-status", _VERSION, locked)
    validate_schema_instance("service-status", _VERSION, ready_local)
    assert "workflow" not in cast(list[str], locked["capabilities"])
    assert "external_provider" not in cast(list[str], locked["capabilities"])
    assert "external_provider" not in cast(list[str], ready_local["capabilities"])

    for allowed in (_WORKFLOW_METHODS, _ALL_METHODS):
        result: dict[str, JsonValue] = {
            "protocol_version": "1.0",
            "service_version": "0.1.0",
            "service_instance_id": _INSTANCE_ID,
            "service_generation": "1",
            "status": locked,
            "allowed_methods": list(allowed),
            "schema_manifest_digest": _DIGEST,
        }
        validate_schema_instance("control-hello-result", _VERSION, result)

    widened: dict[str, JsonValue] = {
        "protocol_version": "1.0",
        "service_version": "0.1.0",
        "service_instance_id": _INSTANCE_ID,
        "service_generation": "1",
        "status": locked,
        "allowed_methods": cast(list[JsonValue], [*_WORKFLOW_METHODS, "import_codex_jsonl"]),
        "schema_manifest_digest": _DIGEST,
    }
    _assert_invalid("control-hello-result", widened)


def test_v2_hello_adds_only_an_optional_ephemeral_workspace_locator() -> None:
    hello: dict[str, JsonValue] = {
        "protocol_version": "1.0",
        "client_kind": "cli",
        "client_version": "0.1.0",
        "connection_nonce": "0" * 64,
        "schema_manifest_digest": _DIGEST,
        "workspace_locator": {"schema_version": "1.0.0", "path": "/tmp/repository"},
    }
    validate_schema_instance("control-hello", "2.0.0", hello)
    with pytest.raises(ProtocolValueError):
        validate_schema_instance("control-hello", "1.0.0", hello)
    for invalid_locator in (
        {"schema_version": "1.0.0", "path": "relative"},
        {"schema_version": "1.0.0", "path": "/tmp/repository", "extra": True},
        {"schema_version": "2.0.0", "path": "/tmp/repository"},
    ):
        candidate = deepcopy(hello)
        candidate["workspace_locator"] = cast(JsonValue, invalid_locator)
        with pytest.raises(ProtocolValueError):
            validate_schema_instance("control-hello", "2.0.0", candidate)


def test_v2_repository_privacy_bodies_are_closed_and_v1_is_retained() -> None:
    request_schema = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.0.0.schema.json").read_bytes()),
    )
    result_schema = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.0.0.schema.json").read_bytes()),
    )
    request_defs = cast(dict[str, dict[str, Any]], request_schema["$defs"])
    setup_request_union = request_defs["privacy_get_setup_body"]
    assert len(setup_request_union["oneOf"]) == 2
    setup_request = setup_request_union["oneOf"][1]
    assert setup_request["additionalProperties"] is False
    assert setup_request["required"] == ["schema_version"]
    assert setup_request["properties"] == {"schema_version": {"const": "2.0.0"}}
    proposal_request_union = request_defs["privacy_propose_policy_body"]
    assert len(proposal_request_union["oneOf"]) == 2
    proposal_request = proposal_request_union["oneOf"][1]
    assert proposal_request["additionalProperties"] is False
    assert set(proposal_request["required"]) == {
        "schema_version",
        "authority_digest",
        "candidate_policy",
    }
    assert "expected_policy_digest" not in proposal_request["properties"]

    result_defs = cast(dict[str, dict[str, Any]], result_schema["$defs"])
    setup_result_union = result_defs["privacy_get_setup_body"]
    assert len(setup_result_union["oneOf"]) == 2
    setup_result = setup_result_union["oneOf"][1]
    assert setup_result["additionalProperties"] is False
    assert set(setup_result["required"]) == {
        "schema_version",
        "composed_policy",
        "bound_scope",
        "authority_digest",
        "grant_state",
        "migration_state",
        "channel_choices",
        "allowed_blocked_examples",
        "recipes",
        "never_send_editable",
        "privacy_projection",
    }
    assert setup_result["properties"]["never_send_editable"] == {"const": False}
    assert len(result_defs["privacy_propose_policy_body"]["oneOf"]) == 4
    v1_defs = cast(dict[str, dict[str, Any]], _schema("control-request")["$defs"])
    assert "expected_policy_digest" in v1_defs["privacy_propose_policy_body"]["properties"]


def test_v21_appends_cursor_observation_wire_without_rewriting_v2() -> None:
    request_v2 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.0.0.schema.json").read_bytes()),
    )
    request_v21 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.1.0.schema.json").read_bytes()),
    )
    result_v2 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.0.0.schema.json").read_bytes()),
    )
    result_v21 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.1.0.schema.json").read_bytes()),
    )

    request_v2_defs = cast(dict[str, dict[str, Any]], request_v2["$defs"])
    request_v21_defs = cast(dict[str, dict[str, Any]], request_v21["$defs"])
    source_v2 = request_v2_defs["observation_envelope"]["properties"]["source"]
    source_v21 = request_v21_defs["observation_envelope"]["properties"]["source"]
    assert source_v2["enum"] == ["codex_hook", "codex_session_stream"]
    assert source_v21["enum"] == ["codex_hook", "codex_session_stream", "cursor_hook"]

    changed_v2 = request_v2_defs["observation_envelope"]["properties"]["structural_payload"][
        "properties"
    ]["changed_paths_digest"]
    changed_v21 = request_v21_defs["observation_envelope"]["properties"]["structural_payload"][
        "properties"
    ]["changed_paths_digest"]
    assert changed_v2["pattern"] == "^sha256:[0-9a-f]{64}$"
    assert [branch["pattern"] for branch in changed_v21["oneOf"]] == [
        "^sha256:[0-9a-f]{64}$",
        "^hmac-sha256:[0-9a-f]{64}$",
    ]

    result_v2_defs = cast(dict[str, dict[str, Any]], result_v2["$defs"])
    result_v21_defs = cast(dict[str, dict[str, Any]], result_v21["$defs"])
    coverage_v2 = result_v2_defs["observation_status"]["properties"]["source_coverage"]
    coverage_v21 = result_v21_defs["observation_status"]["properties"]["source_coverage"]
    assert set(coverage_v2["properties"]) == {"codex_hook", "codex_session_stream"}
    assert set(coverage_v21["properties"]) == {
        "codex_hook",
        "codex_session_stream",
        "cursor_hook",
    }

    for filename in ("control-request-2.1.0.schema.json", "control-result-2.1.0.schema.json"):
        assert (_ROOT / filename).read_bytes() == _PACKAGE_ROOT.joinpath(filename).read_bytes()


def _cursor_ingest_frame(structural: dict[str, Any]) -> dict[str, Any]:
    return {
        "body": {
            "codex_session_id": "cursor:session-1",
            "envelope": {
                "content_object_refs": [],
                "cursor": {
                    "byte_position": 0,
                    "event_position": 1,
                    "last_source_commitment": "hmac-sha256:" + "0" * 64,
                    "mapping_version": "cursor-local-3.17",
                    "source_generation": 1,
                },
                "event_kind": "session_start",
                "gap_codes": [],
                "receipt_time": "2026-08-24T00:00:00.000Z",
                "session_commitment": "hmac-sha256:" + "1" * 64,
                "source": "cursor_hook",
                "source_identity": "hook:cursor",
                "structural_payload": structural,
            },
        },
        "kind": "call",
        "method": "observation_ingest",
        "protocol_version": "1.0",
        "rpc_id": _RPC_ID,
        "service_generation": "1",
        "service_instance_id": _INSTANCE_ID,
    }


def test_v21_wire_admits_the_exact_cursor_structural_tokens_hook_ingress_sends() -> None:
    """The 2.1.0 request wire must carry every structural key Cursor ingress can emit.

    ``structural_payload`` is ``additionalProperties: false``, so an omitted key here is not a
    lenient default: it makes the observation undeliverable at the boundary.
    """

    frame = _cursor_ingest_frame(
        {
            "capability_profile_id": "cursor-local-3.17",
            "cursor_version": "3.17.8",
            "hook_name": "sessionStart",
            "model_effort": "medium",
            "model_id": "claude-4.5-sonnet",
        }
    )

    validate_schema_instance("control-request", "2.1.0", cast(JsonValue, frame))

    # The domain admits ``:`` in these three structural tokens, so the wire must too or the same
    # class of undeliverable observation returns for a namespaced model identity.
    colon_bearing = _cursor_ingest_frame(
        {"model_id": "anthropic:claude-4.5-sonnet", "model_effort": "high"}
    )
    validate_schema_instance("control-request", "2.1.0", cast(JsonValue, colon_bearing))

    unknown = _cursor_ingest_frame({"cursor_version": "3.17.8", "cursor_workspace_path": "/tmp"})
    with pytest.raises(ProtocolValueError):
        validate_schema_instance("control-request", "2.1.0", cast(JsonValue, unknown))


def test_v22_adds_only_the_claude_hook_source_and_coverage_cell() -> None:
    request_v21 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.1.0.schema.json").read_bytes()),
    )
    request_v22 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.2.0.schema.json").read_bytes()),
    )
    result_v21 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.1.0.schema.json").read_bytes()),
    )
    result_v22 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.2.0.schema.json").read_bytes()),
    )
    source_v21 = request_v21["$defs"]["observation_envelope"]["properties"]["source"]
    source_v22 = request_v22["$defs"]["observation_envelope"]["properties"]["source"]
    assert source_v21["enum"] == ["codex_hook", "codex_session_stream", "cursor_hook"]
    assert source_v22["enum"] == [
        "codex_hook",
        "codex_session_stream",
        "cursor_hook",
        "claude_hook",
    ]
    coverage_v21 = result_v21["$defs"]["observation_status"]["properties"]["source_coverage"]
    coverage_v22 = result_v22["$defs"]["observation_status"]["properties"]["source_coverage"]
    assert set(coverage_v21["properties"]) == {
        "codex_hook",
        "codex_session_stream",
        "cursor_hook",
    }
    assert set(coverage_v22["properties"]) == {
        "claude_hook",
        "codex_hook",
        "codex_session_stream",
        "cursor_hook",
    }
    for filename in ("control-request-2.2.0.schema.json", "control-result-2.2.0.schema.json"):
        assert (_ROOT / filename).read_bytes() == _PACKAGE_ROOT.joinpath(filename).read_bytes()


def test_v23_updates_only_the_status_operation_schema_refs() -> None:
    request_v22 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.2.0.schema.json").read_bytes()),
    )
    request_v23 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.3.0.schema.json").read_bytes()),
    )
    result_v22 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.2.0.schema.json").read_bytes()),
    )
    result_v23 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.3.0.schema.json").read_bytes()),
    )

    old_request_id = request_v22.pop("$id")
    new_request_id = request_v23.pop("$id")
    old_result_id = result_v22.pop("$id")
    new_result_id = result_v23.pop("$id")
    request_v22_text = str(request_v22)
    result_v22_text = str(result_v22)
    assert "status-request-1.0.0.schema.json" in request_v22_text
    assert "status-result-1.0.0.schema.json" in result_v22_text

    def replace_status_version(value: object) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            for key, member in mapping.items():
                if key == "$ref" and isinstance(member, str):
                    replaced = member.replace("status-request-1.1.0", "status-request-1.0.0")
                    mapping[key] = replaced.replace("status-result-1.1.0", "status-result-1.0.0")
                else:
                    replace_status_version(member)
        elif isinstance(value, list):
            for member in cast(list[object], value):
                replace_status_version(member)

    replace_status_version(request_v23)
    replace_status_version(result_v23)
    assert request_v23 == request_v22
    assert result_v23 == result_v22
    assert old_request_id.endswith("control-request-2.2.0.schema.json")
    assert new_request_id.endswith("control-request-2.3.0.schema.json")
    assert old_result_id.endswith("control-result-2.2.0.schema.json")
    assert new_result_id.endswith("control-result-2.3.0.schema.json")
    for filename in ("control-request-2.3.0.schema.json", "control-result-2.3.0.schema.json"):
        assert (_ROOT / filename).read_bytes() == _PACKAGE_ROOT.joinpath(filename).read_bytes()


def test_v24_updates_only_the_publish_operation_schema_ref() -> None:
    request_v23 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.3.0.schema.json").read_bytes()),
    )
    request_v24 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-request-2.4.0.schema.json").read_bytes()),
    )
    result_v23 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.3.0.schema.json").read_bytes()),
    )
    result_v24 = cast(
        dict[str, Any],
        strict_json_parse((_ROOT / "control-result-2.4.0.schema.json").read_bytes()),
    )

    old_request_id = request_v23.pop("$id")
    new_request_id = request_v24.pop("$id")
    old_result_id = result_v23.pop("$id")
    new_result_id = result_v24.pop("$id")
    assert "publish-work-request-1.0.0.schema.json" in str(request_v23)
    assert "publish-work-request-1.1.0.schema.json" in str(request_v24)

    def restore_publish_version(value: object) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, object], value)
            for key, member in mapping.items():
                if key == "$ref" and isinstance(member, str):
                    mapping[key] = member.replace(
                        "publish-work-request-1.1.0", "publish-work-request-1.0.0"
                    )
                else:
                    restore_publish_version(member)
        elif isinstance(value, list):
            for member in cast(list[object], value):
                restore_publish_version(member)

    restore_publish_version(request_v24)
    assert request_v24 == request_v23
    assert result_v24 == result_v23
    assert old_request_id.endswith("control-request-2.3.0.schema.json")
    assert new_request_id.endswith("control-request-2.4.0.schema.json")
    assert old_result_id.endswith("control-result-2.3.0.schema.json")
    assert new_result_id.endswith("control-result-2.4.0.schema.json")
    for filename in (
        "control-request-2.4.0.schema.json",
        "control-result-2.4.0.schema.json",
    ):
        assert (_ROOT / filename).read_bytes() == _PACKAGE_ROOT.joinpath(filename).read_bytes()


def test_control_request_and_result_unions_are_exact_and_disjoint() -> None:
    request_schema = _schema("control-request")
    result_schema = _schema("control-result")
    requests = cast(list[dict[str, Any]], request_schema["oneOf"])
    results = cast(list[dict[str, Any]], result_schema["oneOf"])

    assert len(requests) == 32
    assert len(results) == 62
    call_methods: list[str] = []
    cancel_count = 0
    for branch in requests:
        properties = cast(dict[str, dict[str, Any]], branch["properties"])
        assert branch["additionalProperties"] is False
        assert properties["protocol_version"]["const"] == "1.0"
        if properties["kind"]["const"] == "cancel":
            cancel_count += 1
            assert "method" not in properties
            assert "body" not in properties
            assert "target_rpc_id" in properties
        else:
            call_methods.append(cast(str, properties["method"]["const"]))
            assert properties["kind"]["const"] == "call"
            assert "body" in properties
    assert cancel_count == 1
    assert tuple(sorted(call_methods)) == _ALL_METHODS

    result_pairs: list[tuple[str, str]] = []
    for branch in results:
        properties = cast(dict[str, dict[str, Any]], branch["properties"])
        assert branch["additionalProperties"] is False
        method = cast(str, properties["method"]["const"])
        outcome = cast(str, properties["outcome"]["const"])
        result_pairs.append((method, outcome))
        body_ref = cast(str, properties["body"]["$ref"])
        if outcome == "error":
            assert body_ref == "#/$defs/error_body"
    assert tuple(sorted(result_pairs)) == tuple(
        sorted((method, outcome) for method in _ALL_METHODS for outcome in ("error", "ok"))
    )


def test_integration_requests_require_explicit_codex_harness() -> None:
    definitions = cast(dict[str, dict[str, Any]], _schema("control-request")["$defs"])
    preview_branches = cast(list[dict[str, Any]], definitions["integration_preview_body"]["oneOf"])
    execute_body = definitions["integration_execute_body"]
    for body in (*preview_branches, execute_body):
        properties = cast(dict[str, dict[str, Any]], body["properties"])
        assert properties["harness"] == {"const": "codex"}
        assert "harness" in cast(list[str], body["required"])

    bodies: tuple[tuple[str, dict[str, JsonValue]], ...] = (
        (
            "integration_preview",
            {
                "schema_version": "1.0.0",
                "operation": "preview",
                "request_id": _REQUEST_ID,
                "harness": "codex",
                "project_root": "/tmp/project",
                "action": "install",
                "replace_modified": False,
            },
        ),
        (
            "integration_preview",
            {
                "schema_version": "1.0.0",
                "operation": "status",
                "harness": "codex",
                "project_root": "/tmp/project",
            },
        ),
        (
            "integration_execute",
            {
                "schema_version": "1.0.0",
                "request_id": _REQUEST_ID,
                "harness": "codex",
                "project_root": "/tmp/project",
                "action": "install",
                "preview_digest": _DIGEST,
                "explicitly_accepted": True,
                "replace_modified": False,
            },
        ),
    )
    for method, body in bodies:
        request: dict[str, JsonValue] = {
            "kind": "call",
            "protocol_version": "1.0",
            "rpc_id": _RPC_ID,
            "service_instance_id": _INSTANCE_ID,
            "service_generation": "1",
            "method": method,
            "body": body,
        }
        validate_schema_instance("control-request", _VERSION, request)
        for invalid_harness in (None, "claude"):
            invalid = deepcopy(request)
            invalid_body = cast(dict[str, JsonValue], invalid["body"])
            if invalid_harness is None:
                del invalid_body["harness"]
            else:
                invalid_body["harness"] = invalid_harness
            _assert_invalid("control-request", invalid)


def test_envelope_identity_and_error_bodies_fail_closed() -> None:
    request: dict[str, JsonValue] = {
        "kind": "call",
        "protocol_version": "1.0",
        "rpc_id": _RPC_ID,
        "service_instance_id": _INSTANCE_ID,
        "service_generation": "1",
        "method": "service_status",
        "body": {},
    }
    validate_schema_instance("control-request", _VERSION, request)
    for field, bad in (
        ("protocol_version", "0.1"),
        ("rpc_id", "rpc_bad"),
        ("service_instance_id", "svc_bad"),
        ("service_generation", "01"),
        ("method", "unknown"),
        ("body", {"unknown": True}),
    ):
        candidate = deepcopy(request)
        candidate[field] = cast(JsonValue, bad)
        _assert_invalid("control-request", candidate)

    for code in _CONTROL_ERROR_CODES:
        result: dict[str, JsonValue] = {
            "protocol_version": "1.0",
            "rpc_id": _RPC_ID,
            "service_instance_id": _INSTANCE_ID,
            "service_generation": "1",
            "method": "service_status",
            "outcome": "error",
            "body": {"code": code, "retryable": False},
        }
        validate_schema_instance("control-result", _VERSION, result)
        for extra in ("message", "path", "username"):
            widened = deepcopy(result)
            cast(dict[str, JsonValue], widened["body"])[extra] = "not public"
            _assert_invalid("control-result", widened)

    confused = {
        "protocol_version": "1.0",
        "rpc_id": _RPC_ID,
        "service_instance_id": _INSTANCE_ID,
        "service_generation": "1",
        "method": "service_status",
        "outcome": "ok",
        "body": {"code": "internal_error", "retryable": False},
    }
    _assert_invalid("control-result", confused)


def test_status_bounds_backup_privacy_audit_and_confidential_absence() -> None:
    for idle in (60, 86_400):
        status = _valid_status(ready=True)
        status["idle_relock_seconds"] = idle
        validate_schema_instance("service-status", _VERSION, status)
    for idle in (59, 86_401, True, "60", None):
        status = _valid_status(ready=True)
        status["idle_relock_seconds"] = cast(JsonValue, idle)
        _assert_invalid("service-status", status)
    for capabilities in (
        ["workflow", "maintenance"],
        ["workflow", "workflow"],
    ):
        status = _valid_status(ready=True)
        status["capabilities"] = cast(list[JsonValue], capabilities)
        _assert_invalid("service-status", status)

    result_schema = _schema("control-result")
    definitions = cast(dict[str, dict[str, Any]], result_schema["$defs"])
    for definition_name in ("backup_preview_body", "backup_execute_body"):
        definition = definitions[definition_name]
        required = set(cast(list[str], definition["required"]))
        properties = cast(dict[str, Any], definition["properties"])
        assert {"privacy_audit_object_count", "privacy_audit_snapshot_digest"} <= required
        assert properties["privacy_audit_object_count"]["type"] == "integer"
        assert properties["privacy_audit_snapshot_digest"]["pattern"] == "^sha256:[0-9a-f]{64}$"
        assert "privacy_audit_content" not in properties
        assert "privacy_audit_path" not in properties

    forbidden_property_names = {
        "passphrase",
        "secret",
        "unlock",
        "provider_credential",
        "key_locator",
        "pid",
        "username",
    }
    for name in (
        "control-hello",
        "control-hello-result",
        "control-request",
        "control-result",
        "service-status",
    ):
        property_names = {
            key
            for key, value in _walk(_schema(name))
            if key not in {"properties", "$defs"} and value is not None
        }
        assert forbidden_property_names.isdisjoint(property_names)

    # Cross-field lifecycle combinations are intentionally owned by Wave C's ServiceStatus model;
    # the frozen schema only proves bounded structural serialization.
