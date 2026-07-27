"""Bounded structured error mappings for the MCP transport surface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final, cast

from pydantic import ValidationError

from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import SAFE_DETAIL_KEYS, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import FRONTIER_LEAVES, OperationFailureModel

__all__ = [
    "authoring_hint",
    "build_last_resort_internal_error_result",
    "build_public_error_result",
    "safe_validation_locations",
    "sanitize_unknown_tool_name",
    "tool_error_envelope",
]

_SAFE_LOCATION_SEGMENTS: Final = frozenset(
    {
        "actor",
        "actor_id",
        "actor_type",
        "artifact_refs",
        "asserted_by",
        "at_frontier",
        "causal_parents",
        "client",
        "cursor",
        "disposition",
        "display_name",
        "event_drafts",
        "event_id",
        "evidence_refs",
        "expected_frontier",
        "external_ref",
        "filter",
        "finding_frontier",
        "finding_id",
        "format",
        # Frontier leaves. Without them a wrong key inside expected_frontier/at_frontier projects
        # to the parent object and the caller learns only that "something" there is wrong.
        "head_digest",
        "include",
        "integration",
        "kind",
        "limit",
        "max_findings",
        "mode",
        "name",
        "occurred_at",
        "payload",
        "policy_packs",
        "protocol_version",
        "publication_channel",
        "reason",
        "redaction_profile",
        "request_id",
        "requested_view",
        "schema",
        "schema_name",
        "schema_version",
        "scope",
        "sequence",
        "session_id",
        "subject_state",
        "task_id",
        "task_title",
        "version",
        "view",
        "waiver_expiry",
        "waiver_scope",
        "workspace_ref",
        "writer_id",
    }
)
_SAFE_VALIDATION_REASONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "missing": "missing",
        "extra_forbidden": "extra_forbidden",
        "literal_error": "invalid_value",
        "enum": "invalid_value",
        "bool_type": "invalid_type",
        "dict_type": "invalid_type",
        "int_type": "invalid_type",
        "list_type": "invalid_type",
        "string_type": "invalid_type",
        "tuple_type": "invalid_type",
    }
)
_MAX_VALIDATION_LOCATIONS: Final = 8
# Bounded so the hint stays a hint: a long enum dump would bury the field locations it explains.
_MAX_HINT_FIELDS: Final = 3
_MAX_HINT_ENUM_MEMBERS: Final = 8
_MAX_HINT_PATTERN_CHARS: Final = 96
_LOCAL_DEFS_PREFIX: Final = "#/$defs/"


def _pointer_for_location(
    location: object, *, project_parent_on_unsafe_leaf: bool = False
) -> str | None:
    if not isinstance(location, Sequence) or isinstance(location, str | bytes | bytearray):
        return None
    segments: list[str] = []
    for item in cast(Sequence[str | int], location):
        if type(item) is str:
            if item not in _SAFE_LOCATION_SEGMENTS:
                # Forbidden extras often name an untrusted key (e.g. client.id). Prefer the
                # allowlisted parent path over dropping the whole location or allowlisting "id".
                if project_parent_on_unsafe_leaf and segments:
                    break
                return None
            segments.append(item)
        elif type(item) is int and 0 <= item <= 100:
            # An index communicates shape, not a rejected value. Keep it bounded.
            segments.append(str(item))
        else:
            return None
        if len(segments) > 8:
            return None
    if not segments:
        return ""
    return "/" + "/".join(segments)


def safe_validation_locations(exc: object) -> tuple[dict[str, str], ...]:
    """Project Pydantic failures to allowlisted locations and bounded reason tokens."""

    if not isinstance(exc, ValidationError):
        return ()
    projected: list[dict[str, str]] = []
    try:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
    except BaseException:
        return ()
    for item in errors:
        raw_reason = item.get("type")
        if type(raw_reason) is not str:
            continue
        pointer = _pointer_for_location(
            item.get("loc"),
            # Project to the nearest allowlisted parent so untrusted leaf keys (payload fields,
            # extras) never appear, while still naming a useful location such as /event_drafts/0.
            project_parent_on_unsafe_leaf=True,
        )
        # Empty pointers (model-level failures with no path) are not actionable; omit them.
        if pointer is None or pointer == "":
            continue
        reason = _SAFE_VALIDATION_REASONS.get(raw_reason, "invalid_type_or_value")
        projected.append({"field": pointer, "reason": reason})
        if len(projected) == _MAX_VALIDATION_LOCATIONS:
            break
    return tuple(projected)


def authoring_hint(schema: object, locations: Sequence[Mapping[str, str]]) -> str:
    """Name the admitted values for the rejected top-level fields, from the frozen schema.

    An agent that cannot author `start` cannot use the product at all, and the 2026-07-27 dogfood
    burned two calls plus a source-reading detour on exactly that: an empty object, then a guessed
    request-id shape and a mode that is not an admitted value. Field locations alone did not close
    the gap because they say where, not what.

    Every character comes from the checked-in presentation schema — enum members and the worked
    example's own keys — so no caller-controlled text can reach the message.
    """

    if not isinstance(schema, Mapping):
        return ""
    document = cast(Mapping[str, JsonValue], schema)
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        return ""
    fields = cast(Mapping[str, JsonValue], properties)
    parts: list[str] = []
    seen: set[str] = set()
    for location in locations:
        pointer = location.get("field", "")
        # Only top-level scalar fields; nested pointers would need the $defs walk and the example
        # already shows their shape.
        if not pointer.startswith("/") or pointer.count("/") != 1:
            continue
        name = pointer[1:]
        if name in seen or name not in fields:
            continue
        seen.add(name)
        admitted = _admitted_values(_resolved(document, fields[name]))
        if admitted:
            parts.append(f"{name} admits {admitted}")
        if len(parts) == _MAX_HINT_FIELDS:
            break
    if _has_example(document):
        parts.append("see the examples entry in this tool's input schema for a complete request")
    if not parts:
        return ""
    return " Hint: " + "; ".join(parts) + "."


def _resolved(document: Mapping[str, JsonValue], field: JsonValue) -> JsonValue:
    """Follow one local ``$defs`` reference. Identifier fields are refs, not inline enums."""

    if not isinstance(field, Mapping):
        return field
    reference = cast(Mapping[str, JsonValue], field).get("$ref")
    if type(reference) is not str or not reference.startswith(_LOCAL_DEFS_PREFIX):
        return field
    definitions = document.get("$defs")
    if not isinstance(definitions, Mapping):
        return field
    # One hop only: a chain would need cycle handling for no practical gain.
    return cast(Mapping[str, JsonValue], definitions).get(
        reference.removeprefix(_LOCAL_DEFS_PREFIX), field
    )


def _admitted_values(field: JsonValue) -> str:
    if not isinstance(field, Mapping):
        return ""
    source = cast(Mapping[str, JsonValue], field)
    raw_enum = source.get("enum")
    if isinstance(raw_enum, list):
        members = [str(item) for item in cast(list[JsonValue], raw_enum) if type(item) is str]
        if members and len(members) <= _MAX_HINT_ENUM_MEMBERS:
            return ", ".join(sorted(members))
    raw_const = source.get("const")
    if type(raw_const) is str:
        return raw_const
    # A bounded pattern is the only way to state an identifier's shape. The dogfood agent invented
    # a free-form request id because nothing named the required one.
    raw_pattern = source.get("pattern")
    if type(raw_pattern) is str and len(raw_pattern) <= _MAX_HINT_PATTERN_CHARS:
        return f"values matching {raw_pattern}"
    return ""


def _has_example(document: Mapping[str, JsonValue]) -> bool:
    examples = document.get("examples")
    return isinstance(examples, list) and bool(cast(list[JsonValue], examples))


def sanitize_unknown_tool_name(name: object) -> str:
    """Return one caller-independent message for an unregistered tools/call name."""

    del name
    return "The requested tool is not registered."


def _validated_failure(
    public_error: Mapping[str, object], request_id: str | None
) -> dict[str, JsonValue]:
    candidate: dict[str, object] = {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "ok": False,
        "error": dict(public_error),
    }
    if request_id is not None:
        candidate["request_id"] = request_id
    validated = OperationFailureModel.model_validate(candidate)
    return cast(
        dict[str, JsonValue],
        validated.model_dump(mode="json", by_alias=True, exclude_unset=True),
    )


def tool_error_envelope(
    error: PublicOperationError, *, request_id: str | None = None
) -> dict[str, JsonValue]:
    """Map one bound public application error to the common operation-failure shape."""

    if type(error) is not PublicOperationError:
        raise TypeError("public_operation_error_wrong_type")
    return _validated_failure(error.as_public_dict(), request_id)


def build_public_error_result(
    code: PublicErrorCode,
    message: str,
    retryable: bool,
    correlation_id: str,
    *,
    request_id: str | None = None,
    safe_details: object | None = None,
) -> dict[str, JsonValue]:
    """Build and schema-check one exact public operation-failure result."""

    if type(safe_details) is tuple:
        locations = cast(tuple[Mapping[str, str], ...], safe_details)
        public_error: dict[str, object] = {
            "code": code.value,
            "message": message,
            "retryable": retryable,
            "correlation_id": correlation_id,
            "safe_details": {
                "fields": tuple(location["field"] for location in locations),
                "reasons": tuple(location["reason"] for location in locations),
            },
        }
        return _validated_failure(public_error, request_id)
    error = PublicOperationError(
        code,
        message,
        retryable,
        correlation_id=correlation_id,
        safe_details=safe_details,
    )
    return tool_error_envelope(error, request_id=request_id)


_LAST_RESORT_CORRELATION_ID: Final = new_id(IdKind.CORRELATION)
_LAST_RESORT_MESSAGE: Final = "An internal error occurred."


def build_last_resort_internal_error_result() -> dict[str, JsonValue]:
    """Return a helper-free, request-independent failure admitted by every tool schema."""

    # Deliberately do not call constructors, validators, serializers, ID generators, or summary
    # helpers here. The shared values were admitted when this module initialized.
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "ok": False,
        "error": {
            "code": "INTERNAL_ERROR",
            "message": _LAST_RESORT_MESSAGE,
            "retryable": False,
            "correlation_id": _LAST_RESORT_CORRELATION_ID,
        },
    }


# Fail startup if the supposedly universal literal branch ever leaves the shared schema contract.
OperationFailureModel.model_validate(build_last_resort_internal_error_result())

# Frontier leaves are hand-authored on every state-sensitive publish, and getting one wrong is the
# single most common routine payload mistake. They are frozen schema names already trusted as
# SAFE_DETAIL_KEYS, so failing to locate one is a diagnostic gap, not a safety property.
if not set(FRONTIER_LEAVES) <= _SAFE_LOCATION_SEGMENTS:
    raise RuntimeError("safe_location_segments_missing_frontier_leaf")
if not set(FRONTIER_LEAVES) <= set(SAFE_DETAIL_KEYS):
    raise RuntimeError("safe_detail_keys_missing_frontier_leaf")
