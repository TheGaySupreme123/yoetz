"""Host-held semantic check advisory (issue #857): facts, closed texts, one retry."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from yoetz.application.serving_route import read_serving_route, record_serving_route
from yoetz.cli.host_denial_advisory import (
    GRANT_UNCONFIRMED_REASONS,
    HostDenialFacts,
    admission_absent_advisory,
    compose_permission_denied_advisory,
    read_host_denial_facts,
)
from yoetz.ports.control import ControlClientKind, ControlError
from yoetz.protocol.canonical import JsonValue

_CANARIES = ("CLAIM_CANARY", "toolu_CANARY", "CWD_CANARY", "classifier prose CANARY")


def _payload(**overrides: JsonValue) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "session_id": "claude-held",
        "hook_event_name": "PermissionDenied",
        "tool_name": "mcp__plugin_yoetz_yoetz__check",
        "tool_input": {"claim": "CLAIM_CANARY"},
        "tool_use_id": "toolu_CANARY",
        "reason": "denied_by_classifier",
        "source": "auto",
        "cwd": "/private/CWD_CANARY",
        "permission_mode": "auto",
    }
    payload.update(overrides)
    return payload


class _GrantClient:
    def __init__(
        self,
        *,
        state: str = "ready",
        grant_state: object = "granted",
        enabled: object = True,
        raise_reason: str | None = None,
    ) -> None:
        self.state = state
        self.grant_state = grant_state
        self.enabled = enabled
        self.raise_reason = raise_reason
        self.calls: list[str] = []
        self.closed = False

    async def service_status(self) -> object:
        self.calls.append("service_status")
        if self.raise_reason is not None:
            raise ControlError(self.raise_reason)
        return SimpleNamespace(state=SimpleNamespace(value=self.state), state_reason=None)

    async def privacy_get_setup(self, request: object, *, deadline_ms: int | None = None) -> object:
        self.calls.append("privacy_get_setup")
        assert deadline_ms is not None
        assert isinstance(request, Mapping)
        return {
            "grant_state": self.grant_state,
            "composed_policy": {
                "channel_policies": [{"channel": "llm_inference", "enabled": self.enabled}]
            },
        }

    async def close(self) -> None:
        self.closed = True


def _connector(client: object):
    async def connect(kind: ControlClientKind) -> object:
        assert kind is ControlClientKind.CLI
        return client

    return connect


# --- serving route marker -------------------------------------------------------------------


def test_serving_route_round_trips_per_host_and_reads_unknown_as_none(tmp_path: Path) -> None:
    assert read_serving_route("claude", _state=tmp_path) is None
    assert record_serving_route("claude", "policy", _state=tmp_path)
    assert record_serving_route("codex", "strict", _state=tmp_path)
    assert read_serving_route("claude", _state=tmp_path) == "policy"
    assert read_serving_route("codex", _state=tmp_path) == "strict"
    assert read_serving_route("cursor", _state=tmp_path) is None
    # A later bridge start for the same host replaces its entry and leaves the others.
    assert record_serving_route("claude", "strict", _state=tmp_path)
    assert read_serving_route("claude", _state=tmp_path) == "strict"
    assert read_serving_route("codex", _state=tmp_path) == "strict"
    document = json.loads((tmp_path / "integrations/serving-routes.json").read_text("utf-8"))
    assert set(document) == {"hosts", "schema"}
    assert set(document["hosts"]["claude"]) == {"recorded_at", "route_profile"}


def test_serving_route_refuses_generic_hosts_and_unknown_profiles(tmp_path: Path) -> None:
    assert not record_serving_route("generic", "policy", _state=tmp_path)
    assert not record_serving_route("claude", "unknown", _state=tmp_path)
    assert read_serving_route("generic", _state=tmp_path) is None
    assert not (tmp_path / "integrations/serving-routes.json").exists()


def test_serving_route_reads_an_invalid_document_as_unobserved(tmp_path: Path) -> None:
    store = tmp_path / "integrations"
    store.mkdir(mode=0o700)
    (store / "serving-routes.json").write_text('{"schema":"other","hosts":{"claude":{}}}')
    assert read_serving_route("claude", _state=tmp_path) is None
    (store / "serving-routes.json").write_text("not json")
    assert read_serving_route("claude", _state=tmp_path) is None


# --- facts ----------------------------------------------------------------------------------


def test_facts_confirm_the_grant_only_from_a_ready_service_that_reports_granted_and_enabled(
    tmp_path: Path,
) -> None:
    record_serving_route("claude", "policy", _state=tmp_path)
    client = _GrantClient()
    facts = read_host_denial_facts(
        "claude", str(tmp_path), connect=_connector(client), _state=tmp_path
    )
    assert facts == HostDenialFacts("confirmed", "policy", "absent")
    assert facts.review_authorized
    assert facts.unconfirmed_reason is None
    assert client.calls == ["service_status", "privacy_get_setup"]
    assert client.closed


@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (_GrantClient(grant_state="missing"), "grant_absent"),
        (_GrantClient(enabled=False), "grant_not_permitting"),
        (_GrantClient(grant_state="weird"), "grant_unverifiable"),
        (_GrantClient(enabled="yes"), "grant_unverifiable"),
        (_GrantClient(state="locked"), "vault_locked"),
        (_GrantClient(state="failed"), "service_unavailable"),
        (_GrantClient(state="starting"), "service_unavailable"),
        (_GrantClient(state="unlocking"), "vault_locked"),
        (_GrantClient(raise_reason="vault_locked"), "vault_locked"),
        (_GrantClient(raise_reason="service_draining"), "service_unavailable"),
    ],
)
def test_facts_name_why_the_grant_could_not_be_confirmed(
    tmp_path: Path, client: _GrantClient, expected: str
) -> None:
    record_serving_route("claude", "policy", _state=tmp_path)
    facts = read_host_denial_facts(
        "claude", str(tmp_path), connect=_connector(client), _state=tmp_path
    )
    assert facts.grant == expected
    assert facts.unconfirmed_reason == expected
    assert not facts.review_authorized
    assert expected in GRANT_UNCONFIRMED_REASONS


def test_facts_without_a_service_or_locator_never_confirm(tmp_path: Path) -> None:
    record_serving_route("claude", "policy", _state=tmp_path)

    async def failing(_kind: ControlClientKind) -> object:
        raise OSError("socket missing")

    assert (
        read_host_denial_facts("claude", str(tmp_path), connect=failing, _state=tmp_path).grant
        == "service_unavailable"
    )
    assert (
        read_host_denial_facts(
            "claude", str(tmp_path), connect=_connector(_GrantClient()), skip_service=True
        ).grant
        == "service_unavailable"
    )
    assert (
        read_host_denial_facts("claude", None, connect=_connector(_GrantClient())).grant
        == "grant_unverifiable"
    )


def test_facts_report_the_route_and_admission_as_unread_rather_than_guessed(
    tmp_path: Path,
) -> None:
    facts = read_host_denial_facts(
        "claude", str(tmp_path), connect=_connector(_GrantClient()), _state=tmp_path
    )
    assert facts.grant == "confirmed"
    assert facts.route_profile is None
    assert facts.unconfirmed_reason == "route_unobserved"
    record_serving_route("claude", "strict", _state=tmp_path)
    strict = read_host_denial_facts(
        "claude", str(tmp_path), connect=_connector(_GrantClient()), _state=tmp_path
    )
    assert strict.unconfirmed_reason == "route_strict"
    assert not strict.review_authorized


# --- advisory composition -------------------------------------------------------------------


def _confirmed() -> HostDenialFacts:
    return HostDenialFacts("confirmed", "policy", "absent")


def test_confirmed_grant_offers_exactly_one_retry_per_session_and_tool_call(
    tmp_path: Path,
) -> None:
    first = compose_permission_denied_advisory(_payload(), _confirmed(), _state=tmp_path)
    assert first.case == "grant_confirmed"
    assert first.retry is True
    assert first.diagnostic == "host_denial_retry_offered"
    assert "Retry the identical check once" in first.additional_context
    assert "you already authorized AI-powered review" in first.system_message

    second = compose_permission_denied_advisory(_payload(), _confirmed(), _state=tmp_path)
    assert second.case == "grant_confirmed"
    assert second.retry is False
    assert second.diagnostic == "host_denial_retry_exhausted"
    assert "Do not retry on your own" in second.additional_context
    assert "manual approval" in second.system_message

    other_call = compose_permission_denied_advisory(
        _payload(tool_use_id="toolu_OTHER"), _confirmed(), _state=tmp_path
    )
    assert other_call.retry is True
    other_session = compose_permission_denied_advisory(
        _payload(session_id="claude-other"), _confirmed(), _state=tmp_path
    )
    assert other_session.retry is True

    marker = (tmp_path / "observation/host-denial-retries.json").read_bytes()
    for canary in (b"claude-held", b"toolu_CANARY", b"toolu_OTHER", b"claude-other"):
        assert canary not in marker


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": "no_verdict"},
        {"tool_use_id": None},
        {"session_id": None},
        {"tool_use_id": ""},
    ],
)
def test_confirmed_grant_never_retries_without_a_verdict_or_a_bounded_identity(
    tmp_path: Path, overrides: dict[str, JsonValue]
) -> None:
    payload = _payload()
    for key, value in overrides.items():
        if value is None:
            del payload[key]
        else:
            payload[key] = value
    advisory = compose_permission_denied_advisory(payload, _confirmed(), _state=tmp_path)
    assert advisory.case == "grant_confirmed"
    assert advisory.retry is False
    assert advisory.diagnostic == "host_denial_retry_exhausted"
    assert not (tmp_path / "observation/host-denial-retries.json").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "permission_rule"},
        {"source": "hook"},
        {"source": "rule"},
        {"reason": "denied_by_rule"},
    ],
)
def test_the_owners_own_rule_is_never_retried(
    tmp_path: Path, overrides: dict[str, JsonValue]
) -> None:
    advisory = compose_permission_denied_advisory(
        _payload(**overrides), _confirmed(), _state=tmp_path
    )
    assert advisory.case == "owner_rule"
    assert advisory.retry is False
    assert advisory.diagnostic == "host_denial_grant_unconfirmed"
    assert advisory.additional_context.startswith("The owner's own permission rule held")
    assert "do not retry" in advisory.additional_context
    assert not (tmp_path / "observation/host-denial-retries.json").exists()


@pytest.mark.parametrize(
    "facts",
    [
        HostDenialFacts("service_unavailable", "policy", "absent"),
        HostDenialFacts("vault_locked", "policy", None),
        HostDenialFacts("grant_absent", "policy", "absent"),
        HostDenialFacts("grant_not_permitting", "policy", "present"),
        HostDenialFacts("grant_unverifiable", None, None),
        HostDenialFacts("confirmed", None, "absent"),
        HostDenialFacts("confirmed", "strict", "absent"),
    ],
)
def test_an_unconfirmed_grant_pauses_with_a_closed_reason_and_no_retry(
    tmp_path: Path, facts: HostDenialFacts
) -> None:
    advisory = compose_permission_denied_advisory(_payload(), facts, _state=tmp_path)
    assert advisory.case == "grant_unconfirmed"
    assert advisory.retry is False
    assert advisory.diagnostic == "host_denial_grant_unconfirmed"
    reason = facts.unconfirmed_reason
    assert reason in GRANT_UNCONFIRMED_REASONS
    assert f"(reason: {reason})" in advisory.additional_context
    assert f"({reason})" in advisory.system_message
    assert "stop and ask the user before any retry" in advisory.additional_context
    assert not (tmp_path / "observation/host-denial-retries.json").exists()


def test_every_advisory_text_is_bounded_and_echoes_nothing_from_the_host(tmp_path: Path) -> None:
    payload = _payload(reason="classifier prose CANARY", source="auto_mode")
    seen: list[str] = []
    for facts in (
        _confirmed(),
        _confirmed(),
        HostDenialFacts("grant_absent", "policy", "absent"),
        HostDenialFacts("confirmed", "policy", "present"),
    ):
        advisory = compose_permission_denied_advisory(payload, facts, _state=tmp_path)
        seen.extend((advisory.additional_context, advisory.system_message))
    rule = compose_permission_denied_advisory(
        _payload(source="permission_rule"), _confirmed(), _state=tmp_path
    )
    seen.extend((rule.additional_context, rule.system_message))
    for text in seen:
        assert 0 < len(text) <= 2_000
        for canary in _CANARIES:
            assert canary not in text
        assert str(tmp_path) not in text


# --- SessionStart admission line -------------------------------------------------------------


def _admit_claude(root: Path) -> None:
    (root / ".claude").mkdir(mode=0o700)
    (root / ".claude/settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["mcp__yoetz__check"]}}), encoding="utf-8"
    )


def test_admission_line_names_the_host_command_only_under_all_three_conditions(
    tmp_path: Path,
) -> None:
    record_serving_route("claude", "policy", _state=tmp_path)
    client = _GrantClient()
    line = admission_absent_advisory(
        "claude", str(tmp_path), connect=_connector(client), _state=tmp_path
    )
    assert line is not None
    assert "yoetz integrate claude admission grant" in line
    assert "no admission entry" in line
    assert len(line) <= 400
    assert client.calls == ["service_status", "privacy_get_setup"]


def test_admission_line_stays_silent_when_any_fact_is_unread_or_unfavorable(
    tmp_path: Path,
) -> None:
    client = _GrantClient()
    connect = _connector(client)
    # No serving-route record: silent, and no service round-trip is spent.
    assert (
        admission_absent_advisory("codex", str(tmp_path), connect=connect, _state=tmp_path) is None
    )
    record_serving_route("codex", "strict", _state=tmp_path)
    assert (
        admission_absent_advisory("codex", str(tmp_path), connect=connect, _state=tmp_path) is None
    )
    record_serving_route("codex", "policy", _state=tmp_path)
    # Grant not permitting: silent.
    denied = _GrantClient(enabled=False)
    assert (
        admission_absent_advisory(
            "codex", str(tmp_path), connect=_connector(denied), _state=tmp_path
        )
        is None
    )
    assert client.calls == []
    # Local-only pass: no service read, so the grant is unconfirmed and the line stays silent.
    assert (
        admission_absent_advisory(
            "codex", str(tmp_path), connect=connect, skip_service=True, _state=tmp_path
        )
        is None
    )
    assert admission_absent_advisory("codex", None, connect=connect, _state=tmp_path) is None
    assert admission_absent_advisory("codex", str(tmp_path), connect=connect, _state=tmp_path)


def test_admission_line_is_silent_once_the_host_carries_an_admission_entry(tmp_path: Path) -> None:
    record_serving_route("claude", "policy", _state=tmp_path)
    _admit_claude(tmp_path)
    client = _GrantClient()
    assert (
        admission_absent_advisory(
            "claude", str(tmp_path), connect=_connector(client), _state=tmp_path
        )
        is None
    )
    assert client.calls == []
    facts = read_host_denial_facts(
        "claude", str(tmp_path), connect=_connector(client), _state=tmp_path
    )
    assert facts.admission_state == "present"
    assert cast(object, facts.grant) == "confirmed"


def test_a_grant_read_that_outlives_the_deadline_is_unconfirmed(tmp_path: Path) -> None:
    import anyio

    record_serving_route("claude", "policy", _state=tmp_path)

    class _Slow(_GrantClient):
        async def service_status(self) -> object:
            await anyio.sleep(5)
            return await super().service_status()

    client = _Slow()
    facts = read_host_denial_facts(
        "claude", str(tmp_path), connect=_connector(client), _state=tmp_path, deadline_ms=50
    )
    assert facts.grant == "service_unavailable"
    assert not facts.review_authorized
    assert client.closed
