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
    "privacy_get_effective",
    "privacy_get_setup",
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


def test_control_request_and_result_unions_are_exact_and_disjoint() -> None:
    request_schema = _schema("control-request")
    result_schema = _schema("control-result")
    requests = cast(list[dict[str, Any]], request_schema["oneOf"])
    results = cast(list[dict[str, Any]], result_schema["oneOf"])

    assert len(requests) == 26
    assert len(results) == 50
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
