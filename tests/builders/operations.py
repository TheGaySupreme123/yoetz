"""Explicit request/result builders for the six workflow operations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal, cast

from .ids import validate_test_id

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type OperationName = Literal["start", "publish_work", "check", "respond", "status", "receipt"]

_MAX_JSON_DEPTH = 64
_MAX_SAFE_INTEGER = 2**53 - 1
_RESERVED_ENVELOPE_FIELDS = frozenset({"schema_version", "request_id"})


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


def _copy_fields(fields: Mapping[str, JsonValue], *, reason: str) -> dict[str, JsonValue]:
    copied = _copy_json(fields)
    if not isinstance(copied, dict):
        raise TypeError(reason)
    return copied


def _merge_parts(*parts: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for part in parts:
        for key, value in part.items():
            if key in result:
                raise ValueError("operation_field_collision")
            result[key] = value
    return result


def _envelope(*, schema_version: str, request_id: str) -> dict[str, JsonValue]:
    if schema_version != "1.0.0":
        raise ValueError("schema_version_unsupported")
    return {
        "schema_version": schema_version,
        "request_id": validate_test_id("request", request_id),
    }


def _request(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontier: Mapping[str, JsonValue],
    fields: Mapping[str, JsonValue],
    frontier_required: bool,
) -> dict[str, JsonValue]:
    identity_fields = _copy_fields(identity, reason="identity_wrong_type")
    frontier_fields = _copy_fields(frontier, reason="frontier_wrong_type")
    body_fields = _copy_fields(fields, reason="operation_fields_wrong_type")
    if not identity_fields:
        raise ValueError("request_identity_required")
    if frontier_required and not frontier_fields:
        raise ValueError("frontier_required")
    if any(
        reserved in identity_fields or reserved in frontier_fields or reserved in body_fields
        for reserved in _RESERVED_ENVELOPE_FIELDS
    ):
        raise ValueError("reserved_envelope_field")
    return _merge_parts(
        _envelope(schema_version=schema_version, request_id=request_id),
        identity_fields,
        frontier_fields,
        body_fields,
    )


def _result(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontiers: Mapping[str, JsonValue],
    outcome: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    identity_fields = _copy_fields(identity, reason="identity_wrong_type")
    frontier_fields = _copy_fields(frontiers, reason="frontier_wrong_type")
    outcome_fields = _copy_fields(outcome, reason="outcome_wrong_type")
    if not identity_fields:
        raise ValueError("result_identity_required")
    if not frontier_fields:
        raise ValueError("frontier_required")
    if not outcome_fields:
        raise ValueError("outcome_required")
    if any(
        reserved in identity_fields or reserved in frontier_fields or reserved in outcome_fields
        for reserved in _RESERVED_ENVELOPE_FIELDS
    ):
        raise ValueError("reserved_envelope_field")
    return _merge_parts(
        _envelope(schema_version=schema_version, request_id=request_id),
        identity_fields,
        frontier_fields,
        outcome_fields,
    )


def start_request(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    fields: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _request(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontier={},
        fields=fields,
        frontier_required=False,
    )


def publish_work_request(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontier: Mapping[str, JsonValue],
    fields: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _request(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontier=frontier,
        fields=fields,
        frontier_required=True,
    )


def check_request(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontier: Mapping[str, JsonValue],
    fields: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _request(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontier=frontier,
        fields=fields,
        frontier_required=True,
    )


def respond_request(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontier: Mapping[str, JsonValue],
    fields: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _request(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontier=frontier,
        fields=fields,
        frontier_required=True,
    )


def status_request(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontier: Mapping[str, JsonValue],
    fields: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Build status with an explicit frontier mapping, which may encode an absent frontier."""

    return _request(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontier=frontier,
        fields=fields,
        frontier_required=True,
    )


def receipt_request(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontier: Mapping[str, JsonValue],
    fields: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _request(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontier=frontier,
        fields=fields,
        frontier_required=True,
    )


def start_result(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontiers: Mapping[str, JsonValue],
    outcome: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _result(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontiers=frontiers,
        outcome=outcome,
    )


def publish_work_result(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontiers: Mapping[str, JsonValue],
    outcome: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _result(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontiers=frontiers,
        outcome=outcome,
    )


def check_result(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontiers: Mapping[str, JsonValue],
    outcome: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _result(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontiers=frontiers,
        outcome=outcome,
    )


def respond_result(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontiers: Mapping[str, JsonValue],
    outcome: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _result(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontiers=frontiers,
        outcome=outcome,
    )


def status_result(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontiers: Mapping[str, JsonValue],
    outcome: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _result(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontiers=frontiers,
        outcome=outcome,
    )


def receipt_result(
    *,
    schema_version: str,
    request_id: str,
    identity: Mapping[str, JsonValue],
    frontiers: Mapping[str, JsonValue],
    outcome: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return _result(
        schema_version=schema_version,
        request_id=request_id,
        identity=identity,
        frontiers=frontiers,
        outcome=outcome,
    )


def derive_case(
    value: Mapping[str, JsonValue],
    /,
    *,
    replacements: Mapping[str, JsonValue],
    remove: Sequence[str],
) -> dict[str, JsonValue]:
    """Derive a negative/variant case with every mutation stated by the caller."""

    result = _copy_fields(value, reason="operation_value_wrong_type")
    replacement_fields = _copy_fields(replacements, reason="replacement_wrong_type")
    if len(remove) != len(set(remove)):
        raise ValueError("duplicate_removed_field")
    for key in remove:
        if key not in result:
            raise ValueError("removed_field_absent")
        del result[key]
    result.update(replacement_fields)
    return result
