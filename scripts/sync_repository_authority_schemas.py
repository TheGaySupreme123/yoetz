#!/usr/bin/env python3
"""Generate immutable service-control schemas for repository authority."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Final, cast

from yoetz.protocol.canonical import canonical_encode

_ROOT: Final = Path(__file__).resolve().parents[1]
_SERVICE: Final = _ROOT / "schemas" / "service"
_PRIVACY_POLICY: Final = "https://schemas.yoetz.dev/0.1/privacy/privacy-policy-1.0.0.schema.json"
_DIGEST: Final[dict[str, Any]] = {
    "maxLength": 71,
    "minLength": 71,
    "pattern": "^sha256:[0-9a-f]{64}$",
    "type": "string",
}
_ORDINARY_CONTENT_CAPTURE_PROFILES: Final = (
    "claude-code-ordinary-observation-v1",
    "cursor-ordinary-observation-v1",
)


def _load(name: str) -> dict[str, Any]:
    return json.loads((_SERVICE / f"{name}-1.0.0.schema.json").read_text(encoding="utf-8"))


def _with_id(name: str, version: str, document: dict[str, Any]) -> dict[str, Any]:
    generated = copy.deepcopy(document)
    generated["$id"] = f"https://schemas.yoetz.dev/0.1/service/{name}-{version}.schema.json"
    return generated


def _hello() -> dict[str, Any]:
    generated = _with_id("control-hello", "2.0.0", _load("control-hello"))
    generated["properties"]["workspace_locator"] = {
        "additionalProperties": False,
        "properties": {
            "path": {
                "maxLength": 8192,
                "minLength": 1,
                "pattern": "^/[^\\u0000\\r\\n]*$",
                "type": "string",
            },
            "schema_version": {"const": "1.0.0"},
        },
        "required": ["schema_version", "path"],
        "type": "object",
    }
    generated["properties"]["presentation_context"] = {
        "additionalProperties": False,
        "properties": {
            "output_is_controlling_tty": {"type": "boolean"},
            "render_mode": {
                "enum": ["human_readable", "machine_readable"],
                "type": "string",
            },
        },
        "required": ["render_mode", "output_is_controlling_tty"],
        "type": "object",
    }
    return generated


def _request() -> dict[str, Any]:
    generated = _with_id("control-request", "2.0.0", _load("control-request"))
    definitions = generated["$defs"]
    # Domain source identities are structural tokens and the hook mapper deliberately prefixes
    # them with ``hook:``. The legacy schema token omitted the admitted colon.
    definitions["observation_envelope"]["properties"]["source_identity"]["pattern"] = (
        "^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$"
    )
    definitions["observation_content_chunk"] = {
        "additionalProperties": False,
        "properties": {
            "content_kind": {
                "enum": [
                    "visible_user_message",
                    "visible_assistant_message",
                    "visible_subagent_message",
                    "tool_input",
                    "tool_output",
                    "changed_file",
                    "workspace_diff",
                    "approved_check_output",
                    "unsupported_visible_payload",
                    "workspace_locator",
                ],
                "type": "string",
            },
            "correlation_identity": {
                "maxLength": 128,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$",
                "type": "string",
            },
            "source_commitment": {
                "maxLength": 76,
                "minLength": 76,
                "pattern": "^hmac-sha256:[0-9a-f]{64}$",
                "type": "string",
            },
            "media_type": {
                "maxLength": 128,
                "minLength": 3,
                "pattern": "^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$",
                "type": "string",
            },
            "part_index": {"maximum": 15, "minimum": 0, "type": "integer"},
            "part_count": {"maximum": 16, "minimum": 1, "type": "integer"},
            "content_b64": {
                "maxLength": 699052,
                "minLength": 4,
                "pattern": (
                    "^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/][AQgw]==|"
                    "[A-Za-z0-9+/]{2}[AEIMQUYcgkosw048]=)?$"
                ),
                "type": "string",
            },
            "redacted": {"type": "boolean"},
        },
        "required": [
            "content_kind",
            "correlation_identity",
            "source_commitment",
            "media_type",
            "part_index",
            "part_count",
            "content_b64",
            "redacted",
        ],
        "type": "object",
    }
    definitions["observation_ingest_body"] = {
        "additionalProperties": False,
        "properties": {
            "codex_session_id": {
                "maxLength": 128,
                "minLength": 1,
                "pattern": "^[^/\\\\\\u0000]+$",
                "type": "string",
            },
            "content_chunks": {
                "items": {"$ref": "#/$defs/observation_content_chunk"},
                "maxItems": 16,
                "minItems": 1,
                "type": "array",
            },
            "envelope": {"$ref": "#/$defs/observation_envelope"},
        },
        "required": ["codex_session_id", "envelope"],
        "type": "object",
    }
    legacy_setup = definitions["privacy_get_setup_body"]
    definitions["privacy_get_setup_body"] = {
        "oneOf": [
            legacy_setup,
            {
                "additionalProperties": False,
                "properties": {"schema_version": {"const": "2.0.0"}},
                "required": ["schema_version"],
                "type": "object",
            },
        ]
    }
    legacy_proposal = definitions["privacy_propose_policy_body"]
    definitions["privacy_propose_policy_body"] = {
        "oneOf": [
            legacy_proposal,
            {
                "additionalProperties": False,
                "properties": {
                    "authority_digest": copy.deepcopy(_DIGEST),
                    "candidate_policy": {"$ref": _PRIVACY_POLICY},
                    "schema_version": {"const": "2.0.0"},
                },
                "required": ["schema_version", "authority_digest", "candidate_policy"],
                "type": "object",
            },
        ]
    }
    return generated


def _setup_result() -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": {
            "allowed_blocked_examples": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "allowed": {"type": "boolean"},
                        "code": {
                            "maxLength": 64,
                            "minLength": 1,
                            "pattern": "^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$",
                            "type": "string",
                        },
                    },
                    "required": ["code", "allowed"],
                    "type": "object",
                },
                "maxItems": 16,
                "type": "array",
                "uniqueItems": True,
            },
            "authority_digest": copy.deepcopy(_DIGEST),
            "bound_scope": {
                "allOf": [
                    {"$ref": f"{_PRIVACY_POLICY}#/$defs/authorization_scope"},
                    {
                        "properties": {"kind": {"const": "workspace"}},
                        "required": ["kind"],
                        "type": "object",
                    },
                ]
            },
            "channel_choices": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "capability_state": {
                            "enum": ["available", "unsupported"],
                            "type": "string",
                        },
                        "channel": {"$ref": f"{_PRIVACY_POLICY}#/$defs/egress_channel"},
                        "enabled": {"type": "boolean"},
                    },
                    "required": ["channel", "enabled", "capability_state"],
                    "type": "object",
                },
                "maxItems": 8,
                "type": "array",
                "uniqueItems": True,
            },
            "composed_policy": {"$ref": _PRIVACY_POLICY},
            "grant_state": {"enum": ["granted", "missing"], "type": "string"},
            "migration_state": {
                "enum": [
                    "not_applicable",
                    "legacy_route_available",
                    "first_repository_available",
                    "consumed",
                ],
                "type": "string",
            },
            "never_send_editable": {"const": False},
            "privacy_projection": {
                "$ref": (
                    "https://schemas.yoetz.dev/0.1/common/"
                    "operation-result-1.0.0.schema.json#/$defs/privacy_projection"
                )
            },
            "recipes": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "privacy_profile": {"$ref": f"{_PRIVACY_POLICY}#/$defs/privacy_profile"},
                        "recipe": {
                            "enum": [
                                "private",
                                "metadata_only",
                                "assisted_review",
                                "expanded_review",
                                "custom",
                            ],
                            "type": "string",
                        },
                        "review_context_profile": {
                            "$ref": f"{_PRIVACY_POLICY}#/$defs/review_context_profile"
                        },
                        "review_selection": {
                            "$ref": f"{_PRIVACY_POLICY}#/$defs/review_selection_policy"
                        },
                    },
                    "required": [
                        "recipe",
                        "privacy_profile",
                        "review_context_profile",
                        "review_selection",
                    ],
                    "type": "object",
                },
                "maxItems": 5,
                "type": "array",
                "uniqueItems": True,
            },
            "schema_version": {"const": "2.0.0"},
        },
        "required": [
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
        ],
        "type": "object",
    }


def _result() -> dict[str, Any]:
    generated = _with_id("control-result", "2.0.0", _load("control-result"))
    definitions = generated["$defs"]
    # The ready observation handler returns the bounded domain result directly. The legacy
    # request/status wrapper was replaced when routing ownership moved to the service coordinator.
    definitions["observation_ingest_body"] = copy.deepcopy(definitions["observation_ingest_result"])
    definitions["privacy_get_setup_body"] = {
        "oneOf": [definitions["privacy_get_setup_body"], _setup_result()]
    }
    legacy_proposal = definitions["privacy_propose_policy_body"]
    v2_proposal = copy.deepcopy(legacy_proposal)
    for branch in v2_proposal["oneOf"]:
        branch["properties"]["schema_version"] = {"const": "2.0.0"}
    definitions["privacy_propose_policy_body"] = {
        "oneOf": [*legacy_proposal["oneOf"], *v2_proposal["oneOf"]]
    }
    return generated


def _cursor_request() -> dict[str, Any]:
    generated = _with_id("control-request", "2.1.0", _request())
    envelope = generated["$defs"]["observation_envelope"]
    envelope["properties"]["source"]["enum"].append("cursor_hook")
    changed_paths_digest = copy.deepcopy(
        envelope["properties"]["structural_payload"]["properties"]["changed_paths_digest"]
    )
    envelope["properties"]["structural_payload"]["properties"]["changed_paths_digest"] = {
        "oneOf": [
            changed_paths_digest,
            {
                "maxLength": 76,
                "minLength": 76,
                "pattern": "^hmac-sha256:[0-9a-f]{64}$",
                "type": "string",
            },
        ]
    }
    # Cursor ingress admits three additional bounded structural tokens beside ``codex_version``.
    # ``structural_payload`` is ``additionalProperties: false``, so omitting them here would make
    # every Cursor observation that carries a Cursor/model identity undeliverable on the wire.
    # The domain validates exactly these three through its structural-token rule, whose token
    # alphabet admits ``:``; the wire pattern must not be narrower or the same class of
    # undeliverable observation returns for a colon-bearing model identity.
    for name in ("cursor_version", "model_id", "model_effort"):
        envelope["properties"]["structural_payload"]["properties"][name] = {
            "maxLength": 128,
            "minLength": 1,
            "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$",
            "type": "string",
        }
    return generated


def _cursor_result() -> dict[str, Any]:
    generated = _with_id("control-result", "2.1.0", _result())
    source_coverage = generated["$defs"]["observation_status"]["properties"]["source_coverage"]
    source_coverage["properties"]["cursor_hook"] = {"type": "boolean"}
    return generated


def _claude_request() -> dict[str, Any]:
    generated = _with_id("control-request", "2.2.0", _cursor_request())
    generated["$defs"]["observation_envelope"]["properties"]["source"]["enum"].append("claude_hook")
    return generated


def _claude_result() -> dict[str, Any]:
    generated = _with_id("control-result", "2.2.0", _cursor_result())
    source_coverage = generated["$defs"]["observation_status"]["properties"]["source_coverage"]
    source_coverage["properties"]["claude_hook"] = {"type": "boolean"}
    return generated


def _replace_schema_ref(value: Any, old: str, new: str) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        for key, member in mapping.items():
            if key == "$ref" and member == old:
                mapping[key] = new
            else:
                _replace_schema_ref(member, old, new)
    elif isinstance(value, list):
        for member in cast(list[Any], value):
            _replace_schema_ref(member, old, new)


def _status_v23_request() -> dict[str, Any]:
    generated = _with_id("control-request", "2.3.0", _claude_request())
    _replace_schema_ref(
        generated,
        "https://schemas.yoetz.dev/0.1/operations/status-request-1.0.0.schema.json",
        "https://schemas.yoetz.dev/0.1/operations/status-request-1.1.0.schema.json",
    )
    return generated


def _status_v23_result() -> dict[str, Any]:
    generated = _with_id("control-result", "2.3.0", _claude_result())
    _replace_schema_ref(
        generated,
        "https://schemas.yoetz.dev/0.1/operations/status-result-1.0.0.schema.json",
        "https://schemas.yoetz.dev/0.1/operations/status-result-1.1.0.schema.json",
    )
    return generated


def _claim_v24_request() -> dict[str, Any]:
    generated = _with_id("control-request", "2.4.0", _status_v23_request())
    # Serving-host identity is new to the current, unreleased control schema.
    # Keep released 1.0/2.0 bytes and earlier request versions unchanged.
    for branch in generated["oneOf"]:
        properties = branch.get("properties", {})
        if properties.get("method", {}).get("const") == "check":
            properties["host_profile"] = {
                "enum": ["generic", "codex", "claude", "cursor"],
                "type": "string",
            }
    # Host/profile pairing metadata is likewise new to the current schema.
    # Keep the local observation envelope's structural keys closed for every
    # released request version while allowing the issue #607 ingress contract
    # through the active 2.4 control path.
    structural_properties = generated["$defs"]["observation_envelope"]["properties"][
        "structural_payload"
    ]["properties"]
    structural_properties.update(
        {
            "pairing_mode": {
                "enum": ["paired", "post_only"],
                "type": "string",
            },
            "correlation_kind": {
                "enum": ["tool_call_id", "generation_id", "none"],
                "type": "string",
            },
            "generation_id": {
                "maxLength": 128,
                "minLength": 1,
                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$",
                "type": "string",
            },
        }
    )
    _replace_schema_ref(
        generated,
        "https://schemas.yoetz.dev/0.1/operations/publish-work-request-1.0.0.schema.json",
        "https://schemas.yoetz.dev/0.1/operations/publish-work-request-1.1.0.schema.json",
    )

    def admit_policy_versions(value: Any) -> None:
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            if mapping.get("$ref") == _PRIVACY_POLICY:
                mapping.pop("$ref")
                mapping["anyOf"] = [
                    {"$ref": _PRIVACY_POLICY},
                    {
                        "$ref": "https://schemas.yoetz.dev/0.1/privacy/privacy-policy-1.1.0.schema.json"
                    },
                ]
            else:
                for child in mapping.values():
                    admit_policy_versions(child)
        elif isinstance(value, list):
            for child in cast(list[Any], value):
                admit_policy_versions(child)

    admit_policy_versions(generated)
    # The ordinary native-host content arm is a new optional request member.
    # Keep the released and intermediate control schemas byte-identical: only
    # the current unreleased 2.4 request may carry this authorization-bound
    # profile selector.
    ingest = generated["$defs"]["observation_ingest_body"]
    ingest["properties"]["content_capture_profile"] = {
        "enum": list(_ORDINARY_CONTENT_CAPTURE_PROFILES),
        "type": "string",
    }
    return generated


def _semantic_provenance_v24_result() -> dict[str, Any]:
    generated = _with_id("control-result", "2.4.0", _status_v23_result())
    for old, new in (
        ("check-result-1.0.0", "check-result-1.1.0"),
        ("receipt-result-1.0.0", "receipt-result-1.1.0"),
        ("status-result-1.1.0", "status-result-1.2.0"),
    ):
        _replace_schema_ref(
            generated,
            f"https://schemas.yoetz.dev/0.1/operations/{old}.schema.json",
            f"https://schemas.yoetz.dev/0.1/operations/{new}.schema.json",
        )
    _replace_schema_ref(
        generated,
        _PRIVACY_POLICY,
        "https://schemas.yoetz.dev/0.1/privacy/privacy-policy-1.1.0.schema.json",
    )
    return generated


def _documents() -> dict[Path, bytes]:
    documents = {
        ("control-hello", "2.0.0"): _hello(),
        ("control-hello-result", "2.0.0"): _with_id(
            "control-hello-result", "2.0.0", _load("control-hello-result")
        ),
        ("control-request", "2.0.0"): _request(),
        ("control-result", "2.0.0"): _result(),
        ("control-hello", "2.1.0"): _with_id("control-hello", "2.1.0", _hello()),
        ("control-hello-result", "2.1.0"): _with_id(
            "control-hello-result",
            "2.1.0",
            _with_id("control-hello-result", "2.0.0", _load("control-hello-result")),
        ),
        ("control-request", "2.1.0"): _cursor_request(),
        ("control-result", "2.1.0"): _cursor_result(),
        ("control-hello", "2.2.0"): _with_id("control-hello", "2.2.0", _hello()),
        ("control-hello-result", "2.2.0"): _with_id(
            "control-hello-result",
            "2.2.0",
            _with_id("control-hello-result", "2.0.0", _load("control-hello-result")),
        ),
        ("control-request", "2.2.0"): _claude_request(),
        ("control-result", "2.2.0"): _claude_result(),
        ("control-hello", "2.3.0"): _with_id("control-hello", "2.3.0", _hello()),
        ("control-hello-result", "2.3.0"): _with_id(
            "control-hello-result",
            "2.3.0",
            _with_id("control-hello-result", "2.0.0", _load("control-hello-result")),
        ),
        ("control-request", "2.3.0"): _status_v23_request(),
        ("control-result", "2.3.0"): _status_v23_result(),
        ("control-hello", "2.4.0"): _with_id("control-hello", "2.4.0", _hello()),
        ("control-hello-result", "2.4.0"): _with_id(
            "control-hello-result",
            "2.4.0",
            _with_id("control-hello-result", "2.0.0", _load("control-hello-result")),
        ),
        ("control-request", "2.4.0"): _claim_v24_request(),
        ("control-result", "2.4.0"): _semantic_provenance_v24_result(),
    }
    return {
        _SERVICE / f"{name}-{version}.schema.json": canonical_encode(document)
        for (name, version), document in documents.items()
    }


def _sync_manifest(documents: dict[Path, bytes], *, write: bool) -> list[Path]:
    manifest_path = _ROOT / "schemas" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    members = cast(list[dict[str, Any]], manifest["members"])
    by_path = {cast(str, member["path"]): member for member in members}
    owning_models = {
        "control-hello": "control hello wire helper",
        "control-hello-result": "control hello result wire helper",
        "control-request": "ControlRequest",
        "control-result": "ControlResult",
    }
    mismatches: list[Path] = []
    for path, payload in documents.items():
        relative = path.relative_to(_ROOT / "schemas").as_posix()
        name_and_version = path.name.removesuffix(".schema.json")
        name, version = name_and_version.rsplit("-", 1)
        expected = {
            "$id": f"https://schemas.yoetz.dev/0.1/{relative}",
            "artifact_role": "local-control",
            "byte_length": len(payload),
            "media_type": "application/schema+json",
            "owning_model": owning_models[name],
            "path": relative,
            "schema_kind": "request_result",
            "schema_version": version,
            "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }
        existing = by_path.get(relative)
        if existing != expected:
            mismatches.append(path.relative_to(_ROOT))
            if write:
                if existing is None:
                    members.append(expected)
                else:
                    existing.clear()
                    existing.update(expected)
    if write:
        members.sort(key=lambda member: cast(str, member["path"]).encode("ascii"))
        manifest_path.write_bytes(canonical_encode(manifest))
        return []
    return mismatches


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    documents = _documents()
    mismatches: list[Path] = []
    for path, expected in documents.items():
        if args.write:
            path.write_bytes(expected)
        elif not path.is_file() or path.read_bytes() != expected:
            mismatches.append(path.relative_to(_ROOT))
    mismatches.extend(_sync_manifest(documents, write=args.write))
    if mismatches:
        for path in mismatches:
            print(f"generated schema stale: {path}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
