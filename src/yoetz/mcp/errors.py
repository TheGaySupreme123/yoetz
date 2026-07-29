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
        # Frozen payload field names. Without them a nested enum failure (action_kind) projects
        # only to /event_drafts/N and the hint cannot name admitted members.
        "action_id",
        "action_kind",
        "artifact_refs",
        "asserted_by",
        "assignee_actor_id",
        "at_frontier",
        "authority",
        "causal_parents",
        "change",
        "claim_id",
        "claim_kind",
        "client",
        "command",
        "content_digest",
        "cursor",
        "description",
        "disposition",
        "display_name",
        "dry_run",
        "event_drafts",
        "event_id",
        "evidence_expectation",
        "evidence_id",
        "evidence_kind",
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
        "obligation_changes",
        "obligation_id",
        "obligation_ids",
        "obligation_refs",
        "observed_at",
        "occurred_at",
        "operation_request_id",
        "outcome",
        "payload",
        "plan_version",
        "policy_packs",
        "protocol_version",
        "publication_channel",
        "rationale",
        "reason",
        "redaction_profile",
        "request_id",
        "requested_view",
        "result_id",
        "schema",
        "schema_name",
        "schema_version",
        "scope",
        "scope_description",
        "sequence",
        "session_id",
        "statement",
        "status",
        "strength",
        "subject_state",
        "summary",
        "supersedes_plan_version",
        "supporting_refs",
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
        # Closed object-rule tokens projected from schema-side dependentRequired / if-then.
        "paired_field_required": "paired_field_required",
        "conditional_field_required": "conditional_field_required",
    }
)
# ValueError ctx tokens from `_validate_model_against_schema` object-rule projection. Only these
# may replace the generic value_error reason; never trust free-form exception text.
_SAFE_VALUE_ERROR_REASON_TOKENS: Final = frozenset(
    {"paired_field_required", "conditional_field_required"}
)
_MAX_VALIDATION_LOCATIONS: Final = 8
# Bounded so the hint stays a hint: a long enum dump would bury the field locations it explains.
_MAX_HINT_FIELDS: Final = 3
_MAX_HINT_ENUM_MEMBERS: Final = 8
# Schema-name unions (ordinary publish families) are larger than payload enums; keep a separate
# bound so `/event_drafts/N` can name admitted families without dumping unbounded oneOf lists.
_MAX_HINT_SCHEMA_NAMES: Final = 16
_MAX_HINT_PATTERN_CHARS: Final = 96
_MAX_HINT_REF_HOPS: Final = 8
_MAX_HINT_POINTER_SEGMENTS: Final = 8
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


def _reason_from_validation_item(item: Mapping[str, object]) -> str:
    """Map one Pydantic error item to a closed safe reason token."""

    raw_reason = item.get("type")
    if type(raw_reason) is not str:
        return "invalid_type_or_value"
    if raw_reason == "value_error":
        # Object-rule projections stash a closed token on ValueError; do not trust other messages.
        ctx = item.get("ctx")
        if isinstance(ctx, Mapping):
            error = cast(Mapping[object, object], ctx).get("error")
            if isinstance(error, BaseException):
                token = str(error)
                if token in _SAFE_VALUE_ERROR_REASON_TOKENS:
                    return token
        return "invalid_type_or_value"
    return _SAFE_VALIDATION_REASONS.get(raw_reason, "invalid_type_or_value")


def safe_validation_locations(exc: object) -> tuple[dict[str, str], ...]:
    """Project Pydantic failures to allowlisted locations and bounded reason tokens."""

    if not isinstance(exc, ValidationError):
        return ()
    projected: list[dict[str, str]] = []
    try:
        # Context is required to recover closed object-rule reason tokens; inputs stay excluded.
        errors = exc.errors(include_url=False, include_context=True, include_input=False)
    except BaseException:
        return ()
    for item in errors:
        pointer = _pointer_for_location(
            item.get("loc"),
            # Project to the nearest allowlisted parent so untrusted leaf keys (payload fields,
            # extras) never appear, while still naming a useful location such as /event_drafts/0.
            project_parent_on_unsafe_leaf=True,
        )
        # Empty pointers (model-level failures with no path) are not actionable; omit them.
        if pointer is None or pointer == "":
            continue
        reason = _reason_from_validation_item(cast(Mapping[str, object], item))
        projected.append({"field": pointer, "reason": reason})
        if len(projected) == _MAX_VALIDATION_LOCATIONS:
            break
    return tuple(projected)


def authoring_hint(schema: object, locations: Sequence[Mapping[str, str]]) -> str:
    """Name the admitted values for rejected fields, from the frozen presentation schema.

    An agent that cannot author `start` cannot use the product at all, and the 2026-07-27 dogfood
    burned two calls plus a source-reading detour on exactly that: an empty object, then a guessed
    request-id shape and a mode that is not an admitted value. Field locations alone did not close
    the gap because they say where, not what. Nested `/event_drafts/N` failures need the same:
    walk local ``$defs`` so payload enums such as `action_kind` are named.

    Root-level object rules (``dependentRequired``, attach ``if/then``) name the required peer or
    safe alternative from schema metadata only — never from submitted values.

    Every character comes from the checked-in presentation schema — enum members, consts, bounded
    patterns, pairing maps, and the worked example's own keys — so no caller-controlled text can
    reach the message.
    """

    if not isinstance(schema, Mapping):
        return ""
    document = cast(Mapping[str, JsonValue], schema)
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        return ""
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    example_families: list[str] = []
    try:
        object_rule_parts = _object_rule_hint_parts(document, locations)
        for text in object_rule_parts:
            key = (text, "")
            if key in seen:
                continue
            seen.add(key)
            parts.append(text)
            if len(parts) == _MAX_HINT_FIELDS:
                break
        if len(parts) < _MAX_HINT_FIELDS:
            for location in locations:
                pointer = location.get("field", "")
                reason = location.get("reason", "")
                if type(pointer) is not str or not pointer.startswith("/"):
                    continue
                # Object-rule locations already contributed schema-derived pairing text above.
                if reason in _SAFE_VALUE_ERROR_REASON_TOKENS:
                    continue
                label, admitted, family = _hint_for_pointer(document, pointer)
                key = (label, admitted)
                if not admitted or key in seen:
                    continue
                seen.add(key)
                parts.append(f"{label} admits {admitted}")
                if family is not None and family not in example_families:
                    example_families.append(family)
                if len(parts) == _MAX_HINT_FIELDS:
                    break
        if _has_example(document):
            if example_families:
                named = ", ".join(example_families[:_MAX_HINT_FIELDS])
                parts.append(
                    f"see the examples entry for {named} in this tool's input schema "
                    "for a complete request"
                )
            else:
                parts.append(
                    "see the examples entry in this tool's input schema for a complete request"
                )
    except Exception:
        # A hint must never turn a clear validation error into an internal error — including when
        # even the example probe fails. Prefer the checked-in fallback text over silence or raise.
        try:
            has_example = _has_example(document)
        except Exception:
            has_example = True
        return (
            " Hint: see the examples entry in this tool's input schema for a complete request."
            if has_example
            else ""
        )
    if not parts:
        return ""
    return " Hint: " + "; ".join(parts) + "."


def _object_rule_hint_parts(
    document: Mapping[str, JsonValue], locations: Sequence[Mapping[str, str]]
) -> list[str]:
    """Build pairing / conditional hints from schema metadata for object-rule locations."""

    paired_fields: list[str] = []
    conditional_fields: list[str] = []
    for location in locations:
        pointer = location.get("field", "")
        reason = location.get("reason", "")
        if type(pointer) is not str or not pointer.startswith("/"):
            continue
        segments = pointer.removeprefix("/").split("/")
        if len(segments) != 1 or not segments[0]:
            continue
        field = segments[0]
        if field not in _SAFE_LOCATION_SEGMENTS:
            continue
        if reason == "paired_field_required":
            if field not in paired_fields:
                paired_fields.append(field)
        elif reason == "conditional_field_required":
            if field not in conditional_fields:
                conditional_fields.append(field)
    parts: list[str] = []
    if paired_fields:
        parts.extend(_paired_field_hint_parts(document, paired_fields))
    if conditional_fields:
        parts.extend(_conditional_field_hint_parts(document, conditional_fields))
    return parts


def _paired_field_hint_parts(document: Mapping[str, JsonValue], fields: Sequence[str]) -> list[str]:
    """Name required peers from schema ``dependentRequired`` only."""

    deps = document.get("dependentRequired")
    if not isinstance(deps, Mapping):
        return []
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        return []
    prop_names = cast(Mapping[str, JsonValue], properties)
    field_set = set(fields)
    parts: list[str] = []
    seen: set[str] = set()
    for key, peers in cast(Mapping[object, object], deps).items():
        if type(key) is not str or key not in prop_names:
            continue
        if key not in field_set and not (
            isinstance(peers, list)
            and any(type(p) is str and p in field_set for p in cast(list[object], peers))
        ):
            continue
        if not isinstance(peers, list):
            continue
        peer_names = [
            peer for peer in cast(list[object], peers) if type(peer) is str and peer in prop_names
        ]
        if not peer_names:
            continue
        text = f"{key} requires {', '.join(peer_names)}"
        if text in seen:
            continue
        seen.add(text)
        parts.append(text)
        if len(parts) == _MAX_HINT_FIELDS:
            break
    return parts


def _conditional_field_hint_parts(
    document: Mapping[str, JsonValue], fields: Sequence[str]
) -> list[str]:
    """Name safe required alternatives activated by a root-level ``if/then`` rule."""

    del fields  # Field list confirms a conditional failure; alternatives come from the schema.
    properties = document.get("properties")
    if not isinstance(properties, Mapping):
        return []
    prop_names = cast(Mapping[str, JsonValue], properties)
    all_of = document.get("allOf")
    if not isinstance(all_of, list):
        return []
    for branch in cast(list[JsonValue], all_of):
        if not isinstance(branch, Mapping):
            continue
        source = cast(Mapping[str, JsonValue], branch)
        if_node = source.get("if")
        then_node = source.get("then")
        if not isinstance(if_node, Mapping) or not isinstance(then_node, Mapping):
            continue
        condition = _if_const_condition(cast(Mapping[str, JsonValue], if_node), prop_names)
        alternatives = _required_alternatives(cast(Mapping[str, JsonValue], then_node), prop_names)
        if not alternatives:
            continue
        alt_text = " or ".join(alternatives)
        if condition is not None:
            return [f"{condition} requires {alt_text}"]
        return [f"requires {alt_text}"]
    return []


def _if_const_condition(
    if_node: Mapping[str, JsonValue], prop_names: Mapping[str, JsonValue]
) -> str | None:
    """Return ``field value`` for a simple ``if.properties.X.const`` condition, else None."""

    required = if_node.get("required")
    props = if_node.get("properties")
    if not isinstance(props, Mapping):
        return None
    if isinstance(required, list):
        keys = [item for item in cast(list[object], required) if type(item) is str]
    else:
        keys = [key for key in cast(Mapping[object, object], props) if type(key) is str]
    if len(keys) != 1:
        return None
    field = keys[0]
    if field not in prop_names or field not in _SAFE_LOCATION_SEGMENTS:
        return None
    field_schema = cast(Mapping[str, JsonValue], props).get(field)
    if not isinstance(field_schema, Mapping):
        return None
    const = cast(Mapping[str, JsonValue], field_schema).get("const")
    if type(const) is not str or not const.isascii() or len(const) > 64:
        return None
    return f"{field} {const}"


def _required_alternatives(
    then_node: Mapping[str, JsonValue], prop_names: Mapping[str, JsonValue]
) -> list[str]:
    """Return human-readable required alternatives under then.anyOf|oneOf|required."""

    options = then_node.get("anyOf")
    if not isinstance(options, list):
        options = then_node.get("oneOf")
    if isinstance(options, list):
        alts: list[str] = []
        for branch in cast(list[JsonValue], options):
            if not isinstance(branch, Mapping):
                continue
            required = cast(Mapping[str, JsonValue], branch).get("required")
            text = _format_required_list(required, prop_names)
            if text and text not in alts:
                alts.append(text)
            if len(alts) == _MAX_HINT_FIELDS:
                break
        return alts
    return (
        [_format_required_list(then_node.get("required"), prop_names)]
        if then_node.get("required") is not None
        else []
    )


def _format_required_list(required: object, prop_names: Mapping[str, JsonValue]) -> str:
    if not isinstance(required, list):
        return ""
    names = [
        item
        for item in cast(list[object], required)
        if type(item) is str and item in prop_names and item in _SAFE_LOCATION_SEGMENTS
    ]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _hint_for_pointer(
    document: Mapping[str, JsonValue], pointer: str
) -> tuple[str, str, str | None]:
    """Return ``(label, admitted_values, example_family_or_none)`` for one pointer."""

    raw_segments = pointer.removeprefix("/").split("/")
    if not raw_segments or raw_segments == [""]:
        return "", "", None
    if len(raw_segments) > _MAX_HINT_POINTER_SEGMENTS:
        return "", "", None
    segments: list[str | int] = []
    for item in raw_segments:
        if item.isdigit():
            segments.append(int(item))
        else:
            segments.append(item)
    node: JsonValue = cast(JsonValue, document)
    family: str | None = None
    for index, segment in enumerate(segments):
        node = _resolve_local(document, node)
        if type(segment) is int:
            if not isinstance(node, Mapping):
                return "", "", None
            items = cast(Mapping[str, JsonValue], node).get("items")
            if items is None:
                return "", "", None
            node = items
            continue
        # At a discriminated union, prefer the branch that can continue the remaining path.
        remaining = segments[index:]
        selected = _select_union_branch(document, node, remaining)
        if selected is not None:
            branch_node, branch_family = selected
            if branch_family is not None:
                family = branch_family
            node = branch_node
        elif _is_schema_discriminator_path(remaining):
            # Ambiguous oneOf (every branch has schema/name): name admitted families instead of
            # walking the shared base pattern that does not say which schemas are publishable.
            admitted = _union_schema_names(document, node)
            if admitted:
                return "schema.name", admitted, family
        node = _resolve_local(document, node)
        if not isinstance(node, Mapping):
            return "", "", None
        source = cast(Mapping[str, JsonValue], node)
        props = source.get("properties")
        if not isinstance(props, Mapping):
            # Discriminator failures land on schema/name; admit consts from the enclosing oneOf.
            if segment == "name":
                admitted = _union_schema_names(document, node)
                if admitted:
                    return "schema.name", admitted, family
            return "", "", None
        fields = cast(Mapping[str, JsonValue], props)
        if type(segment) is not str or segment not in fields:
            return "", "", None
        node = fields[segment]
    node = _resolve_local(document, node)
    leaf = raw_segments[-1]
    # An array-item pointer (/event_drafts/N) names the admitted event families.
    if leaf.isdigit():
        admitted = _union_schema_names(document, node)
        return ("event family", admitted, family) if admitted else ("", "", None)
    admitted = _admitted_values(node)
    if not admitted and isinstance(node, Mapping):
        admitted = _union_schema_names(document, node)
        if admitted and leaf in {"schema", "name"}:
            return "schema.name" if leaf == "name" else "schema", admitted, family
    if not admitted:
        return "", "", None
    return leaf, admitted, family


def _select_union_branch(
    document: Mapping[str, JsonValue],
    node: JsonValue,
    remaining: Sequence[str | int],
) -> tuple[JsonValue, str | None] | None:
    """Pick the oneOf/anyOf branch that can resolve the remaining pointer, if unique enough."""

    if not isinstance(node, Mapping) or not remaining:
        return None
    source = cast(Mapping[str, JsonValue], node)
    options = source.get("oneOf")
    if not isinstance(options, list):
        options = source.get("anyOf")
    if not isinstance(options, list) or not options:
        return None
    matches: list[tuple[JsonValue, str | None]] = []
    for branch in cast(list[JsonValue], options):
        resolved = _resolve_local(document, branch)
        family = _schema_name_const(document, resolved)
        if _branch_covers_path(document, resolved, remaining):
            matches.append((resolved, family))
    if not matches:
        return None
    # Prefer a branch whose family const is known when several cover the path.
    if len(matches) == 1:
        return matches[0]
    with_family = [item for item in matches if item[1] is not None]
    if len(with_family) == 1:
        return with_family[0]
    # Ambiguous: stay on the union node so callers can name admitted schema names.
    return None


def _branch_covers_path(
    document: Mapping[str, JsonValue],
    branch: JsonValue,
    remaining: Sequence[str | int],
) -> bool:
    node: JsonValue = branch
    for index, segment in enumerate(remaining):
        node = _resolve_local(document, node)
        if type(segment) is int:
            if not isinstance(node, Mapping):
                return False
            items = cast(Mapping[str, JsonValue], node).get("items")
            if items is None:
                return False
            node = items
            continue
        nested = _select_union_branch(document, node, remaining[index:])
        if nested is not None:
            node = nested[0]
        node = _resolve_local(document, node)
        if not isinstance(node, Mapping):
            return False
        props = cast(Mapping[str, JsonValue], node).get("properties")
        if not isinstance(props, Mapping):
            return False
        fields = cast(Mapping[str, JsonValue], props)
        if type(segment) is not str or segment not in fields:
            return False
        node = fields[segment]
    return True


def _schema_name_const(document: Mapping[str, JsonValue], branch: JsonValue) -> str | None:
    node = _resolve_local(document, branch)
    if not isinstance(node, Mapping):
        return None
    props = cast(Mapping[str, JsonValue], node).get("properties")
    if not isinstance(props, Mapping):
        return None
    schema_node = cast(Mapping[str, JsonValue], props).get("schema")
    schema_node = _resolve_local(document, schema_node) if schema_node is not None else None
    if not isinstance(schema_node, Mapping):
        return None
    schema_props = cast(Mapping[str, JsonValue], schema_node).get("properties")
    if not isinstance(schema_props, Mapping):
        return None
    name_node = cast(Mapping[str, JsonValue], schema_props).get("name")
    name_node = _resolve_local(document, name_node) if name_node is not None else None
    if not isinstance(name_node, Mapping):
        return None
    const_name = cast(Mapping[str, JsonValue], name_node).get("const")
    return const_name if type(const_name) is str else None


def _is_schema_discriminator_path(remaining: Sequence[str | int]) -> bool:
    if not remaining:
        return False
    if remaining == ["schema"] or remaining == ["name"]:
        return True
    return list(remaining) == ["schema", "name"]


def _union_schema_names(document: Mapping[str, JsonValue], node: JsonValue) -> str:
    resolved = _resolve_local(document, node)
    if not isinstance(resolved, Mapping):
        return ""
    source = cast(Mapping[str, JsonValue], resolved)
    options = source.get("oneOf")
    if not isinstance(options, list):
        options = source.get("anyOf")
    if not isinstance(options, list):
        return ""
    names: list[str] = []
    for branch in cast(list[JsonValue], options):
        family = _schema_name_const(document, branch)
        if family is not None and family not in names:
            names.append(family)
        if len(names) > _MAX_HINT_SCHEMA_NAMES:
            return ""
    if not names or len(names) > _MAX_HINT_SCHEMA_NAMES:
        return ""
    return ", ".join(sorted(names))


def _resolve_local(document: Mapping[str, JsonValue], field: JsonValue) -> JsonValue:
    """Follow local ``#/$defs/`` references only, bounded against cycles and chains."""

    current = field
    seen: set[str] = set()
    for _ in range(_MAX_HINT_REF_HOPS):
        if not isinstance(current, Mapping):
            return current
        reference = cast(Mapping[str, JsonValue], current).get("$ref")
        if type(reference) is not str or not reference.startswith(_LOCAL_DEFS_PREFIX):
            return current
        if reference in seen:
            return field
        seen.add(reference)
        definitions = document.get("$defs")
        if not isinstance(definitions, Mapping):
            return field
        key = reference.removeprefix(_LOCAL_DEFS_PREFIX)
        nxt = cast(Mapping[str, JsonValue], definitions).get(key)
        if nxt is None:
            return field
        current = nxt
    return field


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
