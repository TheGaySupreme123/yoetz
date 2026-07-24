"""Bounded structured error mappings for the MCP transport surface."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final, cast

from pydantic import ValidationError

from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import OperationFailureModel

__all__ = [
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
        "asserted_by",
        "at_frontier",
        "client",
        "cursor",
        "disposition",
        "display_name",
        "event_drafts",
        "evidence_refs",
        "expected_frontier",
        "external_ref",
        "filter",
        "finding_frontier",
        "finding_id",
        "format",
        "include",
        "integration",
        "kind",
        "limit",
        "max_findings",
        "mode",
        "occurred_at",
        "payload",
        "policy_packs",
        "protocol_version",
        "publication_channel",
        "reason",
        "redaction_profile",
        "request_id",
        "requested_view",
        "schema_name",
        "schema_version",
        "scope",
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
            project_parent_on_unsafe_leaf=raw_reason == "extra_forbidden",
        )
        if pointer is None:
            continue
        reason = _SAFE_VALIDATION_REASONS.get(raw_reason, "invalid_type_or_value")
        projected.append({"field": pointer, "reason": reason})
        if len(projected) == _MAX_VALIDATION_LOCATIONS:
            break
    return tuple(projected)


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
