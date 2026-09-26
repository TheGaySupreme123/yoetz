"""The host-held check advisory: closed facts in, bounded fixed text out (issue #857)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import cast

import pytest

from yoetz.cli import host_hold_advisory as advisory
from yoetz.cli.hook_io import claude_permission_denied_output


def test_grant_is_confirmed_only_from_a_granted_state_with_llm_inference_enabled() -> None:
    granted = {
        "grant_state": "granted",
        "composed_policy": {"channel_policies": [{"channel": "llm_inference", "enabled": True}]},
    }
    assert advisory.grant_from_setup(granted) == "grant_confirmed"
    assert advisory.grant_from_setup({**granted, "grant_state": "missing"}) == "grant_absent"
    disabled = {
        "grant_state": "granted",
        "composed_policy": {"channel_policies": [{"channel": "llm_inference", "enabled": False}]},
    }
    assert advisory.grant_from_setup(disabled) == "grant_absent"
    # Anything short of both facts is unread, never confirmed and never "absent" either.
    assert advisory.grant_from_setup({"grant_state": "granted"}) == "grant_unread"
    assert advisory.grant_from_setup({**granted, "grant_state": "weird"}) == "grant_unread"
    assert (
        advisory.grant_from_setup(
            {"grant_state": "granted", "composed_policy": {"channel_policies": "not-a-list"}}
        )
        == "grant_unread"
    )
    assert advisory.grant_from_setup(None) == "grant_unread"
    assert advisory.grant_from_setup("granted") == "grant_unread"


def test_retry_ledger_offers_each_session_one_retry_and_stores_only_digests(
    tmp_path: Path,
) -> None:
    assert advisory.note_retry_offer("session-A", _state=tmp_path, _now_ms=10) == "first"
    assert advisory.note_retry_offer("session-A", _state=tmp_path, _now_ms=11) == "repeat"
    assert advisory.note_retry_offer("session-B", _state=tmp_path, _now_ms=12) == "first"
    path = tmp_path / "observation/host-hold-retries.json"
    raw = path.read_bytes()
    assert b"session-A" not in raw and b"session-B" not in raw
    ledger = json.loads(raw)
    assert len(ledger) == 2
    assert all(len(key) == 64 for key in ledger)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct((tmp_path / "observation").stat().st_mode & 0o777) == "0o700"


def test_retry_ledger_is_bounded_and_evicts_the_oldest_offer(tmp_path: Path) -> None:
    for index in range(70):
        assert (
            advisory.note_retry_offer(f"session-{index}", _state=tmp_path, _now_ms=index) == "first"
        )
    ledger = json.loads((tmp_path / "observation/host-hold-retries.json").read_bytes())
    assert len(ledger) == 64
    # The six oldest sessions were evicted, so they are offered a retry again; recent ones are not.
    assert advisory.note_retry_offer("session-0", _state=tmp_path, _now_ms=100) == "first"
    assert advisory.note_retry_offer("session-69", _state=tmp_path, _now_ms=101) == "repeat"


def test_retry_ledger_ignores_a_symlinked_or_malformed_file_without_raising(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "observation"
    directory.mkdir(mode=0o700)
    (directory / "host-hold-retries.json").write_text("not json", encoding="utf-8")
    assert advisory.note_retry_offer("session-A", _state=tmp_path, _now_ms=1) == "first"
    assert json.loads((directory / "host-hold-retries.json").read_bytes())
    oversized = directory / "host-hold-retries.json"
    oversized.write_bytes(b"{" + b'"a":1,' * 4_000 + b"}")
    assert advisory.note_retry_offer("session-C", _state=tmp_path, _now_ms=2) == "first"
    assert len(json.loads(oversized.read_bytes())) == 1


def test_retry_ledger_write_failure_is_reported_not_raised(tmp_path: Path) -> None:
    blocked = tmp_path / "observation"
    blocked.write_text("a file where the directory should be", encoding="utf-8")
    assert advisory.note_retry_offer("session-A", _state=tmp_path, _now_ms=1) == "unrecorded"


def _facts(grant: str, admission: str = "absent") -> advisory.HostHoldFacts:
    return advisory.HostHoldFacts(grant, admission)  # type: ignore[arg-type]


def test_every_composed_advisory_is_bounded_fixed_text_with_closed_tokens_only() -> None:
    cases: list[advisory.HostHoldAdvisory] = []
    for grant in sorted(advisory.GRANT_REASONS):
        for admission in sorted(advisory.ADMISSION_STATES):
            for source in ("auto_mode", None, "permission_rule", "hook", "unexpected"):
                for reason in ("classifier_denied", "no_verdict", None):
                    for offer in ("first", "repeat", "unrecorded"):
                        cases.append(
                            advisory.compose_host_hold_advisory(
                                _facts(grant, admission),
                                source=source,
                                reason=reason,
                                retry_offer=offer,  # type: ignore[arg-type]
                            )
                        )
    assert cases
    for case in cases:
        rendered = claude_permission_denied_output(
            case.additional_context, retry=case.retry, system_message=case.system_message
        )
        specific = rendered["hookSpecificOutput"]
        assert isinstance(specific, dict)
        assert specific["additionalContext"] == case.additional_context
        assert len(case.additional_context) <= 2_000
        assert len(case.system_message) <= 2_000
        assert case.additional_context.startswith("Yoetz")
        assert case.system_message.startswith("Yoetz:")
        assert "{" not in case.additional_context and "}" not in case.additional_context
        assert "no provider dispatch occurred" in case.additional_context.lower()
        assert "do not switch to deterministic_only" in case.additional_context
        # A retry is emitted only with a confirmed grant, a classifier verdict and a first offer.
        if case.retry:
            assert case.diagnostic == "host_denial_retry_offered"
            assert "Retry the identical check exactly once now" in case.additional_context
        else:
            assert "exactly once now" not in case.additional_context


def test_retry_requires_every_gate() -> None:
    confirmed = _facts("grant_confirmed")
    first = advisory.compose_host_hold_advisory(
        confirmed, source="auto_mode", reason="classifier_denied", retry_offer="first"
    )
    assert first.retry is True and first.diagnostic == "host_denial_retry_offered"
    absent_source = advisory.compose_host_hold_advisory(
        confirmed, source=None, reason="classifier_denied", retry_offer="first"
    )
    assert absent_source.retry is True
    assert (
        advisory.compose_host_hold_advisory(
            confirmed, source="permission_rule", reason="classifier_denied", retry_offer="first"
        ).retry
        is False
    )
    assert (
        advisory.compose_host_hold_advisory(
            confirmed, source="hook", reason="classifier_denied", retry_offer="first"
        ).diagnostic
        == "host_denial_retry_exhausted"
    )
    assert (
        advisory.compose_host_hold_advisory(
            confirmed, source="auto_mode", reason="no_verdict", retry_offer="first"
        ).retry
        is False
    )
    repeat = advisory.compose_host_hold_advisory(
        confirmed, source="auto_mode", reason="classifier_denied", retry_offer="repeat"
    )
    assert repeat.retry is False and repeat.diagnostic == "host_denial_retry_exhausted"
    unrecorded = advisory.compose_host_hold_advisory(
        confirmed, source="auto_mode", reason="classifier_denied", retry_offer="unrecorded"
    )
    assert unrecorded.retry is False and unrecorded.diagnostic == "host_denial_retry_unrecorded"
    for grant in sorted(advisory.GRANT_REASONS - {"grant_confirmed"}):
        unconfirmed = advisory.compose_host_hold_advisory(
            _facts(grant), source="auto_mode", reason="classifier_denied", retry_offer="first"
        )
        assert unconfirmed.retry is False
        assert unconfirmed.diagnostic == "host_denial_grant_unconfirmed"
        assert f"(reason: {grant})" in unconfirmed.additional_context


def test_admission_state_steers_the_durable_fix_line() -> None:
    def text(admission: str) -> str:
        return advisory.compose_host_hold_advisory(
            _facts("grant_confirmed", admission),
            source="auto_mode",
            reason="classifier_denied",
            retry_offer="first",
        ).additional_context

    assert "yoetz integrate claude admission grant" in text("absent")
    assert "already carries Claude Code's admission entry" in text("present")
    assert "partial Claude Code admission entry" in text("partial")
    assert "wider or conflicting" in text("foreign")
    assert "admission" not in text("unknown").split("gate.")[-1]
    # An unconfirmed grant never advertises admission: that would presuppose the grant.
    unconfirmed = advisory.compose_host_hold_advisory(
        _facts("grant_unread", "absent"),
        source="auto_mode",
        reason="classifier_denied",
        retry_offer="first",
    ).additional_context
    assert "admission grant" not in unconfirmed


def test_read_facts_fail_soft_to_closed_unread_tokens(tmp_path: Path) -> None:
    def runner(fn: object) -> object:
        raise RuntimeError("loop unavailable")

    skipped = advisory.read_host_hold_facts(
        str(tmp_path), connect=None, run_async=runner, skip_service=True
    )
    assert skipped.grant == "service_skipped" and skipped.admission_state == "absent"
    unbound = advisory.read_host_hold_facts(None, connect=None, run_async=runner)
    assert unbound.grant == "workspace_unbound" and unbound.admission_state == "unknown"

    async def connect(_kind: object) -> object:
        raise RuntimeError("no service")

    failed = advisory.read_host_hold_facts(
        str(tmp_path), connect=cast(advisory.PrivacyConnector, connect), run_async=runner
    )
    assert failed.grant == "service_unavailable"
    assert not failed.grant_confirmed


def test_claude_permission_denied_output_shape_bounds_and_blank_handling() -> None:
    rendered = claude_permission_denied_output(
        "  advice  ", retry=True, system_message="  shown to the user  "
    )
    assert rendered == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionDenied",
            "additionalContext": "advice",
            "retry": True,
        },
        "systemMessage": "shown to the user",
    }
    assert claude_permission_denied_output("advice", retry=False) == {
        "hookSpecificOutput": {
            "hookEventName": "PermissionDenied",
            "additionalContext": "advice",
            "retry": False,
        }
    }
    assert claude_permission_denied_output("   ", retry=True, system_message="ignored") == {}
    long = claude_permission_denied_output("x" * 2_500, retry=False, system_message="y" * 2_500)
    specific = long["hookSpecificOutput"]
    assert isinstance(specific, dict)
    assert specific["additionalContext"] == "x" * 2_000
    assert long["systemMessage"] == "y" * 2_000


@pytest.mark.skipif(os.name != "posix", reason="owner-only modes are POSIX")
def test_retry_ledger_lock_file_is_owner_only(tmp_path: Path) -> None:
    advisory.note_retry_offer("session-A", _state=tmp_path, _now_ms=1)
    lock = tmp_path / "observation/.host-hold-retries.lock"
    assert oct(lock.stat().st_mode & 0o777) == "0o600"
