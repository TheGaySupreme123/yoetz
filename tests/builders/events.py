"""Explicit builders for the sixteen event payload families and event drafts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type EventFamily = Literal[
    "session_opened",
    "session_resumed",
    "plan_published",
    "obligation_published",
    "assignment_recorded",
    "decision_recorded",
    "action_recorded",
    "result_recorded",
    "evidence_recorded",
    "claim_recorded",
    "plan_revised",
    "finding_recorded",
    "response_recorded",
    "redaction_recorded",
    "check_recorded",
    "receipt_recorded",
]

SCHEMA_VERSION: Final = "1.0.0"
_MAX_JSON_DEPTH: Final = 64
_MAX_SAFE_INTEGER: Final = 2**53 - 1
_SCHEMA_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class _EventShape:
    required: frozenset[str]
    allowed: frozenset[str]


def _shape(required: set[str], optional: set[str] | None = None) -> _EventShape:
    optional_fields: set[str] = set() if optional is None else optional
    return _EventShape(frozenset(required), frozenset(required | optional_fields))


_EVENT_SHAPES: Final[dict[EventFamily, _EventShape]] = {
    "session_opened": _shape(
        {"task_title", "client_kind", "client_version", "integration", "profile"},
        {"external_ref", "workspace_ref"},
    ),
    "session_resumed": _shape(
        {
            "client_kind",
            "client_version",
            "integration",
            "profile",
            "resumed_frontier",
        }
    ),
    "plan_published": _shape({"plan_version", "summary", "obligation_refs"}, {"scope_exclusions"}),
    "obligation_published": _shape(
        {"obligation_id", "description", "evidence_expectation", "status"},
        {
            "acceptance_criteria",
            "requested_items",
            "source_refs",
            "resolution_evidence_refs",
        },
    ),
    "assignment_recorded": _shape(
        {"assignee_actor_id", "obligation_ids", "scope_description"},
        {"write_policy", "handoff_of"},
    ),
    "decision_recorded": _shape(
        {"statement", "rationale", "authority"},
        {"alternatives", "affected_obligation_ids", "supersedes_event_id"},
    ),
    "action_recorded": _shape(
        {"action_id", "action_kind", "description"},
        {"command", "subject_state", "obligation_refs", "attempted_items"},
    ),
    "result_recorded": _shape(
        {"result_id", "action_id", "outcome"},
        {"exit_status", "summary", "subject_state", "evidence_refs"},
    ),
    "evidence_recorded": _shape(
        {"evidence_id", "evidence_kind", "strength", "observed_at"},
        {
            "reference",
            "captured_object_id",
            "content_digest",
            "description",
            "subject_state",
        },
    ),
    "claim_recorded": _shape(
        {"claim_id", "claim_kind", "statement", "supporting_refs"},
        {"subject_state", "obligation_refs", "disputes_refs"},
    ),
    "plan_revised": _shape(
        {
            "plan_version",
            "supersedes_plan_version",
            "reason",
            "summary",
            "obligation_changes",
        }
    ),
    "finding_recorded": _shape(
        {
            "finding_id",
            "kind",
            "origin",
            "priority",
            "summary",
            "detail",
            "subject_refs",
            "policy_id",
            "policy_version",
            "subject_frontier",
            "coverage",
            "provenance",
        }
    ),
    "response_recorded": _shape(
        {"finding_id", "finding_frontier", "disposition"},
        {"reason", "waiver_scope", "waiver_expiry", "evidence_refs"},
    ),
    "redaction_recorded": _shape(
        {
            "target_event_ids",
            "target_object_ids",
            "method",
            "reason_category",
            "authority",
            "remaining_gap",
        }
    ),
    "check_recorded": _shape(
        {
            "mode",
            "policies",
            "subject_frontier",
            "verdict",
            "returned_finding_ids",
            "suppressed_count",
            "coverage",
            "semantic_status",
            "semantic_reason",
            "semantic_provenance",
            "engine_version",
            "projection_version",
        }
    ),
    "receipt_recorded": _shape(
        {
            "receipt_id",
            "subject_frontier",
            "receipt_digest",
            "receipt_object_id",
            "conclusion_code",
            "redaction_profile",
        }
    ),
}


def _copy_json(value: object, *, depth: int = 0) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError("nesting_too_deep")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise ValueError("integer_out_of_safe_range")
        return value
    if isinstance(value, str):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise ValueError("invalid_utf8") from exc
        return value
    if isinstance(value, list | tuple):
        sequence = cast(Sequence[object], value)
        return [_copy_json(item, depth=depth + 1) for item in sequence]
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        result: dict[str, JsonValue] = {}
        for key, item in mapping.items():
            if not isinstance(key, str):
                raise TypeError("nonstring_object_key")
            if key in result:
                raise ValueError("duplicate_object_key")
            result[key] = _copy_json(item, depth=depth + 1)
        return result
    raise TypeError("unsupported_json_type")


def _payload(family: EventFamily, fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    try:
        shape = _EVENT_SHAPES[family]
    except KeyError as exc:
        raise ValueError("unknown_event_family") from exc
    field_names = set(fields)
    if missing := shape.required - field_names:
        raise ValueError("missing_payload_field:" + ",".join(sorted(missing)))
    if unknown := field_names - shape.allowed:
        raise ValueError("unknown_payload_field:" + ",".join(sorted(unknown)))

    copied = _copy_json(fields)
    if not isinstance(copied, dict):
        raise TypeError("payload_wrong_type")
    _validate_conditional_shape(family, copied)
    return copied


def _validate_conditional_shape(family: EventFamily, fields: dict[str, JsonValue]) -> None:
    if family == "session_opened" and (("external_ref" in fields) != ("workspace_ref" in fields)):
        raise ValueError("attachment_key_incomplete")
    if family == "obligation_published":
        resolved = fields["status"] == "resolved"
        has_resolution = "resolution_evidence_refs" in fields
        if resolved != has_resolution:
            raise ValueError("resolution_evidence_presence_invalid")
    if family == "evidence_recorded":
        strength = fields["strength"]
        if not isinstance(strength, str):
            raise ValueError("evidence_strength_invalid")
        required_by_strength: dict[str, frozenset[str]] = {
            "mutable_reference": frozenset({"reference"}),
            "content_digest": frozenset({"content_digest"}),
            "immutable_snapshot": frozenset({"captured_object_id", "content_digest"}),
            "independently_reproduced": frozenset(
                {"captured_object_id", "content_digest", "subject_state"}
            ),
        }
        if strength == "metadata_only":
            if "description" not in fields and "reference" not in fields:
                raise ValueError("evidence_strength_unsupported")
        elif strength in required_by_strength:
            if not required_by_strength[strength].issubset(fields):
                raise ValueError("evidence_strength_unsupported")
    if family == "response_recorded":
        disposition = fields["disposition"]
        if not isinstance(disposition, str):
            raise ValueError("response_disposition_invalid")
        has_reason = "reason" in fields
        has_scope = "waiver_scope" in fields
        has_expiry = "waiver_expiry" in fields
        if disposition in {"rejected", "waived"} and not has_reason:
            raise ValueError("response_reason_required")
        if disposition == "waived":
            if not has_scope:
                raise ValueError("waiver_scope_required")
        elif has_scope or has_expiry:
            raise ValueError("waiver_field_forbidden")
    if family == "redaction_recorded":
        if fields["target_event_ids"] == [] and fields["target_object_ids"] == []:
            raise ValueError("redaction_target_required")


def build_event_payload(
    family: EventFamily, fields: Mapping[str, JsonValue], /
) -> dict[str, JsonValue]:
    return _payload(family, fields)


def _checked_refs(values: object, *, reason: str) -> list[JsonValue]:
    if isinstance(values, str | bytes):
        raise ValueError(reason)
    if not isinstance(values, Sequence):
        raise TypeError(reason)
    copied: list[str] = []
    for value in cast(Sequence[object], values):
        if not isinstance(value, str) or not value:
            raise ValueError(reason)
        copied.append(value)
    try:
        canonical = sorted(set(copied), key=lambda item: item.encode("ascii", errors="strict"))
    except UnicodeEncodeError as exc:
        raise ValueError(reason) from exc
    if copied != canonical:
        raise ValueError(reason)
    result: list[JsonValue] = []
    result.extend(copied)
    return result


def build_event_draft(
    *,
    event_id: str,
    schema_name: str,
    schema_version: str,
    occurred_at: str,
    causal_parents: Sequence[str],
    payload: Mapping[str, JsonValue],
    artifact_refs: Sequence[str],
    evidence_refs: Sequence[str],
) -> dict[str, JsonValue]:
    """Build one explicit event draft without inferring IDs, time, or references."""

    if not event_id:
        raise ValueError("event_id_required")
    if _SCHEMA_NAME_PATTERN.fullmatch(schema_name) is None:
        raise ValueError("schema_name_invalid")
    if not schema_version:
        raise ValueError("schema_version_required")
    if not occurred_at:
        raise ValueError("occurred_at_required")
    copied_payload = _copy_json(payload)
    if not isinstance(copied_payload, dict):
        raise TypeError("payload_wrong_type")
    return {
        "event_id": event_id,
        "schema": {"name": schema_name, "version": schema_version},
        "occurred_at": occurred_at,
        "causal_parents": _checked_refs(causal_parents, reason="causal_parents_invalid"),
        "payload": copied_payload,
        "artifact_refs": _checked_refs(artifact_refs, reason="artifact_refs_invalid"),
        "evidence_refs": _checked_refs(evidence_refs, reason="evidence_refs_invalid"),
    }


def session_opened(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("session_opened", fields)


def session_resumed(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("session_resumed", fields)


def plan_published(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("plan_published", fields)


def obligation_published(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("obligation_published", fields)


def assignment_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("assignment_recorded", fields)


def decision_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("decision_recorded", fields)


def action_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("action_recorded", fields)


def result_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("result_recorded", fields)


def evidence_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("evidence_recorded", fields)


def claim_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("claim_recorded", fields)


def plan_revised(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("plan_revised", fields)


def finding_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("finding_recorded", fields)


def response_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("response_recorded", fields)


def redaction_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("redaction_recorded", fields)


def check_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("check_recorded", fields)


def receipt_recorded(fields: Mapping[str, JsonValue], /) -> dict[str, JsonValue]:
    return _payload("receipt_recorded", fields)
