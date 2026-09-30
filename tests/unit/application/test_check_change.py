"""Service-side redaction of the check-time change (ADR-031, issue #883)."""

from __future__ import annotations

import pytest

from yoetz.adapters.privacy.local_enforcer import scan_exact_bytes
from yoetz.application import check_change as check_change_module
from yoetz.ports.change_capture import ChangeCaptureUnavailable, CheckChangeCapture

_redacted = getattr(check_change_module, "_redacted")


def _capture(text: bytes) -> CheckChangeCapture:
    return CheckChangeCapture(
        base="task_start",
        text=text,
        tracked_files=1,
        untracked_files=0,
        omitted_files=0,
        truncated=False,
    )


def test_parser_source_with_more_matches_than_one_scan_reports_is_fully_redacted() -> None:
    """Parser code (the meriyah shape) repeats ``token = ...`` far past one scan's finding cap."""

    lines = b"".join(
        f"+  const token = parser.getToken{index}();\n".encode() for index in range(600)
    )
    text = b"diff --git a/src/parser.ts b/src/parser.ts\n" + lines

    redacted = _redacted(_capture(text))

    assert redacted.redacted
    assert scan_exact_bytes(redacted.text) == ()
    assert redacted.text.count(b"[REDACTED]") == 600
    assert b"src/parser.ts" in redacted.text


def test_clean_change_is_returned_unchanged() -> None:
    capture = _capture(b"diff --git a/a.py b/a.py\n+print('kept')\n")

    assert _redacted(capture) is capture


def test_change_that_cannot_be_fully_redacted_is_withheld(monkeypatch: pytest.MonkeyPatch) -> None:
    def never_clean(data: bytes) -> tuple[bytes, bool]:
        return data + b".", True

    monkeypatch.setattr(check_change_module, "redact_sensitive_content", never_clean)

    with pytest.raises(ChangeCaptureUnavailable) as caught:
        _redacted(_capture(b"+token = forever-matching\n"))
    assert caught.value.reason == "redaction_incomplete"
