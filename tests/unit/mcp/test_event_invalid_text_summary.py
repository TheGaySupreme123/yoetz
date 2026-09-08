"""EVENT_INVALID set-order tokens must reach both Claude and Cursor text channels.

Issue #579: Claude Code's generic profile delivers only the text content for isError
results, so a kernel unsorted_set_field rejection arrived as a bare EVENT_INVALID code.
Cursor's native profile copies the exact canonical JSON wire body; both hosts must name
the frozen reason_code and field pointer.
"""

from __future__ import annotations

import json
from typing import Any, cast

from mcp import types

from yoetz.mcp.server import result_from_public_model
from yoetz.mcp.summaries import render_safe_compact_summary
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.models import PublishWorkResultModel

_CORRELATION = "err_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_FIELD = "/event_drafts/4/payload/obligation_refs"
_ENVELOPE_FIELD = "/event_drafts/14/evidence_refs"


def _failure(
    *,
    reason_code: str = "unsorted_set_field",
    field: str = _FIELD,
    message: str = (
        "The event batch is invalid. Every set-valued list is admitted only when its members "
        "are unique and already in ascending ASCII order."
    ),
) -> PublishWorkResultModel:
    return PublishWorkResultModel.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "ok": False,
            "error": {
                "code": "EVENT_INVALID",
                "message": message,
                "retryable": False,
                "correlation_id": _CORRELATION,
                "safe_details": {"field": field, "reason_code": reason_code},
            },
        }
    )


def test_generic_text_summary_names_reason_code_and_field() -> None:
    """Claude Code's isError path is the bounded summary; it must name the kernel rule."""

    wire = _failure().model_dump(mode="json", by_alias=True)
    summary = render_safe_compact_summary(wire)
    assert summary.startswith("Error EVENT_INVALID; retryable: no; correlation:")
    assert f"Reason: unsorted_set_field at {_FIELD}." in summary
    assert len(summary.encode("ascii")) <= 512
    # Caller/message prose never rides the text channel.
    assert "already in ascending ASCII" not in summary


def _text(result: types.CallToolResult) -> str:
    content = cast(list[Any], getattr(result, "content"))
    block = content[0]
    assert isinstance(block, types.TextContent)
    return block.text


def test_generic_and_cursor_host_profiles_both_surface_the_tokens() -> None:
    model = _failure()
    generic = result_from_public_model(model, host_profile="generic")
    cursor = result_from_public_model(model, host_profile="cursor")

    assert generic.isError is True
    assert cursor.isError is True
    generic_text = _text(generic)
    cursor_text = _text(cursor)
    assert "unsorted_set_field" in generic_text
    assert _FIELD in generic_text
    assert generic_text != cursor_text

    structured = cast(dict[str, object], cursor.structuredContent)
    assert cursor_text == canonical_encode(cast(JsonValue, structured)).decode("utf-8")
    parsed = json.loads(cursor_text)
    details = cast(dict[str, object], cast(dict[str, object], parsed["error"])["safe_details"])
    assert details["reason_code"] == "unsorted_set_field"
    assert details["field"] == _FIELD


def test_envelope_ref_mirror_field_is_named_the_same_way() -> None:
    """The second dogfood failure was ref_mirror_mismatch on envelope evidence_refs."""

    summary = render_safe_compact_summary(
        _failure(reason_code="ref_mirror_mismatch", field=_ENVELOPE_FIELD).model_dump(
            mode="json", by_alias=True
        )
    )
    assert f"Reason: ref_mirror_mismatch at {_ENVELOPE_FIELD}." in summary


def test_hostile_field_and_reason_are_dropped() -> None:
    summary = render_safe_compact_summary(
        {
            "ok": False,
            "error": {
                "code": "EVENT_INVALID",
                "retryable": False,
                "correlation_id": _CORRELATION,
                "safe_details": {
                    "reason_code": "not a token",
                    "field": "../../etc/passwd",
                },
            },
        }
    )
    assert "Reason:" not in summary
    assert "passwd" not in summary
    assert "not a token" not in summary


def test_reason_code_alone_is_still_named() -> None:
    summary = render_safe_compact_summary(
        {
            "ok": False,
            "error": {
                "code": "EVENT_INVALID",
                "retryable": False,
                "correlation_id": _CORRELATION,
                "safe_details": {"reason_code": "unsorted_set_field"},
            },
        }
    )
    assert summary.endswith(" Reason: unsorted_set_field.")


def test_cursor_profile_is_not_reduced_to_the_weaker_summary() -> None:
    model = _failure()
    cursor = result_from_public_model(model, host_profile="cursor")
    weaker = render_safe_compact_summary(model.model_dump(mode="json", by_alias=True))
    cursor_text = _text(cursor)
    assert cursor_text != weaker
    assert json.loads(cursor_text)["ok"] is False


def test_generic_content_matches_the_summary_projector() -> None:
    model = _failure()
    result = result_from_public_model(model, host_profile="generic")
    wire = cast(dict[str, Any], result.structuredContent)
    assert _text(result) == render_safe_compact_summary(wire)
