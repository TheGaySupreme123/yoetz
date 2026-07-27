"""The post-commit projection window must never destroy an already-durable operation.

Every assertion here traces to the 2026-07-27 Codex dogfood, where a committed publication and a
pure ``status`` read both surfaced as ``response_projection_failed`` and exact same-``request_id``
replay reproduced the failure instead of the stored success.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

import yoetz.application.service as service_module
import yoetz.service.daemon as daemon_module
from yoetz.ports.control import ControlError, ControlMethod
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import PROTOCOL_REASON_CODES

# Deliberately reaching for module privates: these are the exact internals inside the post-commit
# window, and the public surface cannot express "this internal never raises an unbounded error".
_replace_pointer = cast(
    Callable[[JsonValue, str, JsonValue], JsonValue],
    getattr(service_module, "_replace_pointer"),
)
_segments = cast(Callable[[str], tuple[str, ...]], getattr(service_module, "_segments"))
_READ_ONLY_METHODS = cast(frozenset[ControlMethod], getattr(daemon_module, "_READ_ONLY_METHODS"))
_PROJECTION_EXEMPT_METHODS = cast(
    frozenset[ControlMethod], getattr(daemon_module, "_PROJECTION_EXEMPT_METHODS")
)


def _body() -> JsonValue:
    return {
        "ok": True,
        "page": {"items": [{"summary": "text", "detail": "more"}], "next_cursor": None},
        "accepted_events": [{"summary": "one"}, {"summary": "two"}],
    }


def test_replace_pointer_rewrites_object_and_array_leaves() -> None:
    replaced = _replace_pointer(_body(), "/accepted_events/1/summary", {"omitted": True})
    assert isinstance(replaced, dict)
    events = replaced["accepted_events"]
    assert isinstance(events, tuple)
    first, second = events
    assert isinstance(first, dict) and first["summary"] == "one"
    assert isinstance(second, dict) and second["summary"] == {"omitted": True}


@pytest.mark.parametrize(
    "pointer",
    [
        # The exact shape `_pointer` fabricates when an origin pointer exceeds 256 bytes: a
        # synthetic `/leaf-N` that resolves nowhere in the body. Before this guard it raised a
        # bare KeyError inside the post-commit window.
        "/leaf-7",
        "/page/items/0/absent_field",
        "/page/absent_container/0/summary",
        # A non-numeric or out-of-range array segment must not raise ValueError from int() or
        # IndexError from the list lookup.
        "/accepted_events/not_an_index/summary",
        "/accepted_events/9/summary",
        "/accepted_events/01/summary",
    ],
)
def test_replace_pointer_rejects_unresolvable_pointers_with_a_bounded_error(pointer: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        _replace_pointer(_body(), pointer, {"omitted": True})
    # Bounded and named: the daemon reclassifies anything it does not recognise, so an
    # unbounded KeyError/IndexError here becomes an unreplayable failure for the caller.
    assert str(excinfo.value) in {"projection_pointer_unresolved", "projection_pointer_invalid"}
    assert type(excinfo.value) is ValueError


def test_replace_pointer_rejects_a_pointer_that_is_not_a_pointer() -> None:
    with pytest.raises(ValueError, match="projection_pointer_invalid"):
        _segments("accepted_events/0/summary")


def test_read_only_methods_are_exactly_the_non_appending_ones() -> None:
    # `receipt` appends receipt_recorded and `check` appends check_recorded, so neither is a read.
    # Misclassifying either would tell a caller no durable state changed when it did.
    assert _READ_ONLY_METHODS == frozenset(
        {
            ControlMethod.STATUS,
            ControlMethod.PRIVACY_RECEIPTS_LIST,
            ControlMethod.PRIVACY_RECEIPTS_GET,
        }
    )
    assert ControlMethod.RECEIPT not in _READ_ONLY_METHODS
    assert ControlMethod.CHECK not in _READ_ONLY_METHODS
    assert ControlMethod.PUBLISH_WORK not in _READ_ONLY_METHODS
    # Both privacy reads are projection-exempt, so only STATUS can reach the reclassification.
    assert _READ_ONLY_METHODS - _PROJECTION_EXEMPT_METHODS == {ControlMethod.STATUS}


def test_read_projection_failure_is_a_registered_retryable_reason() -> None:
    assert "read_projection_failed" in PROTOCOL_REASON_CODES
    error = ControlError("read_projection_failed", retryable=True)
    assert error.reason == "read_projection_failed"
    assert error.retryable is True


def test_read_projection_failure_must_be_retryable() -> None:
    # Repeating the read is the remedy, so a non-retryable variant would strand the caller.
    with pytest.raises(ValueError, match="read_projection_error_must_be_retryable"):
        ControlError("read_projection_failed")
