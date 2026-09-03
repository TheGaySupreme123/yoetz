"""Slice B registration-drift tests (issue #537).

Covers the closed diagnostic token, ``mcp status --json`` drift fields, and
the fail-soft SessionStart drift probe. Diagnostic assertions poll the bounded
file with a timeout rather than sleeping a fixed interval.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.applied_mcp_route import read_applied_route, record_applied_route
from yoetz.cli import setup as setup_cli
from yoetz.cli.hook_diagnostics import record_hook_diagnostic
from yoetz.ports.harness_mcp import (
    MCP_SERVE_COMMAND,
    MCP_STRICT_SERVE_COMMAND,
    HarnessBinary,
    McpRegistrationAction,
    McpRegistrationError,
    McpRegistrationObservation,
    McpRegistrationPreview,
    McpRegistrationReason,
    McpRegistrationState,
)
from yoetz.ports.integrations import HarnessId

pytestmark = pytest.mark.anyio

_DIGEST = "sha256:" + "a" * 64
_BINARY = HarnessBinary(
    harness_id=HarnessId.CODEX,
    executable_path="/opt/harness/bin/codex",
    reported_version=None,
    compatibility="untested",
)


def _diagnostic_path(state: Path) -> Path:
    return state / "observation" / "hook-diagnostics.jsonl"


def _wait_for_reason(state: Path, reason: str, *, timeout_s: float = 5.0) -> bool:
    """Poll the diagnostics file until a row names ``reason`` or time runs out."""

    path = _diagnostic_path(state)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        for line in text.splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("reason") == reason:
                return True
        # The writer is synchronous; a missing row after the call returned
        # means no row will arrive, but keep polling to the deadline so the
        # assertion waits on content rather than a fixed sleep.
        time.sleep(0.01)
    return False


def _reasons_in(state: Path) -> list[str]:
    path = _diagnostic_path(state)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    reasons: list[str] = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and type(row.get("reason")) is str:
            reasons.append(row["reason"])
    return reasons


def test_registration_drift_is_a_closed_diagnostic_token(tmp_path: Path) -> None:
    record_hook_diagnostic("registration_drift", "SessionStart", _state=tmp_path)
    assert _wait_for_reason(tmp_path, "registration_drift")
    row = json.loads(_diagnostic_path(tmp_path).read_text(encoding="utf-8").splitlines()[-1])
    assert set(row) == {"event", "reason", "ts"}
    assert row["event"] == "SessionStart"
    assert row["reason"] == "registration_drift"


def _observation(profile: str | None) -> McpRegistrationObservation:
    return McpRegistrationObservation(
        HarnessId.CODEX,
        McpRegistrationState.YOETZ_OWNED,
        profile,  # type: ignore[arg-type]
    )


def _observation_with_state(
    state: McpRegistrationState, profile: str | None
) -> McpRegistrationObservation:
    return McpRegistrationObservation(
        HarnessId.CODEX,
        state,
        profile,  # type: ignore[arg-type]
    )


async def _status_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    registered: str | None,
    applied: str | None,
    state: McpRegistrationState = McpRegistrationState.YOETZ_OWNED,
) -> dict[str, Any]:
    if applied is not None:
        serve = MCP_STRICT_SERVE_COMMAND if applied == "strict" else MCP_SERVE_COMMAND
        record_applied_route(applied, list(serve), list(serve), _DIGEST, _state=tmp_path)
    monkeypatch.setattr(setup_cli, "discover_codex_binaries", lambda: (_BINARY,))

    async def _fake_observe(self: object, _binary: object) -> McpRegistrationObservation:
        return _observation_with_state(state, registered)

    monkeypatch.setattr(
        "yoetz.application.harness_mcp.HarnessMcpService.observe", _fake_observe
    )
    captured: dict[str, Any] = {}

    def _capture(value: object, *, json_output: bool) -> None:
        del json_output
        assert isinstance(value, dict)
        captured.update(value)

    monkeypatch.setattr(setup_cli, "_emit", _capture)
    code = await setup_cli.integrate_mcp(
        "status",
        "codex",
        codex_path=None,
        accept=False,
        preview_digest=None,
        json_output=True,
        _state=tmp_path,
    )
    assert code == 0
    return captured


async def test_mcp_status_json_reports_drift_when_serving_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = await _status_json(monkeypatch, tmp_path, registered="strict", applied="policy")
    assert body["state"] == "yoetz_owned"
    assert body["route_profile"] == "strict"
    assert body["registered_profile"] == "strict"
    assert body["applied_profile"] == "policy"
    assert body["drift_since_install"] is True


async def test_mcp_status_json_reports_no_drift_when_profiles_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = await _status_json(monkeypatch, tmp_path, registered="policy", applied="policy")
    assert body["applied_profile"] == "policy"
    assert body["drift_since_install"] is False


async def test_mcp_status_json_without_record_has_no_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    body = await _status_json(monkeypatch, tmp_path, registered="policy", applied=None)
    assert body["applied_profile"] is None
    assert body["drift_since_install"] is False


async def test_mcp_status_json_absent_with_policy_record_has_no_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ABSENT (registered None) with a policy record never reports drift (B1)."""

    body = await _status_json(
        monkeypatch,
        tmp_path,
        registered=None,
        applied="policy",
        state=McpRegistrationState.ABSENT,
    )
    assert body["state"] == "absent"
    assert body["registered_profile"] is None
    assert body["applied_profile"] == "policy"
    assert body["drift_since_install"] is False


async def test_mcp_status_json_foreign_with_policy_record_has_no_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FOREIGN_PRESENT (registered None) with a policy record never reports drift (B1)."""

    body = await _status_json(
        monkeypatch,
        tmp_path,
        registered=None,
        applied="policy",
        state=McpRegistrationState.FOREIGN_PRESENT,
    )
    assert body["registered_profile"] is None
    assert body["applied_profile"] == "policy"
    assert body["drift_since_install"] is False


async def test_mcp_status_json_unowned_profile_none_with_policy_record_has_no_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Registered None (DUAL/FOREIGN shape) with a policy record never reports drift (B1)."""

    body = await _status_json(monkeypatch, tmp_path, registered=None, applied="policy")
    assert body["registered_profile"] is None
    assert body["applied_profile"] == "policy"
    assert body["drift_since_install"] is False


async def test_mcp_remove_noop_absent_clears_stale_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Policy record + manual host delete + `mcp remove` NOOP clears the record (M1)."""

    record_applied_route(
        "policy",
        list(MCP_SERVE_COMMAND),
        list(MCP_SERVE_COMMAND),
        _DIGEST,
        _state=tmp_path,
    )
    assert read_applied_route(_state=tmp_path) is not None
    monkeypatch.setattr(setup_cli, "discover_codex_binaries", lambda: (_BINARY,))

    preview = McpRegistrationPreview(
        HarnessId.CODEX,
        McpRegistrationAction.NOOP,
        McpRegistrationState.ABSENT,
        (),
        _DIGEST,
        MCP_SERVE_COMMAND,
        "policy",
    )

    async def _fake_preview_unregistration(
        self: object, _binary: object
    ) -> McpRegistrationPreview:
        return preview

    monkeypatch.setattr(
        "yoetz.application.harness_mcp.HarnessMcpService.preview_unregistration",
        _fake_preview_unregistration,
    )
    captured: dict[str, Any] = {}

    def _capture(value: object, *, json_output: bool) -> None:
        del json_output
        assert isinstance(value, dict)
        captured.update(value)

    monkeypatch.setattr(setup_cli, "_emit", _capture)
    code = await setup_cli.integrate_mcp(
        "remove",
        "codex",
        codex_path=None,
        accept=True,
        preview_digest=_DIGEST,
        json_output=True,
        _state=tmp_path,
    )
    assert code == 0
    assert captured.get("action") == "noop"
    # The stale policy record is gone, so later drift reads False.
    assert read_applied_route(_state=tmp_path) is None


def _stub_hook_observation(
    monkeypatch: pytest.MonkeyPatch, *, registered: str | None, fail: bool = False
) -> None:
    monkeypatch.setattr(
        "yoetz.adapters.integrations.codex_discovery.discover_codex_binaries",
        lambda: (_BINARY,),
    )

    async def _fake_observe(self: object, _binary: object) -> McpRegistrationObservation:
        if fail:
            raise McpRegistrationError(McpRegistrationReason.HARNESS_UNAVAILABLE, {})
        return _observation(registered)

    monkeypatch.setattr(
        "yoetz.application.harness_mcp.HarnessMcpService.observe", _fake_observe
    )


def test_session_start_helper_emits_on_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yoetz.cli.hooks import _maybe_record_registration_drift

    record_applied_route(
        "policy",
        list(MCP_SERVE_COMMAND),
        list(MCP_SERVE_COMMAND),
        _DIGEST,
        _state=tmp_path,
    )
    _stub_hook_observation(monkeypatch, registered="strict")

    import anyio

    _maybe_record_registration_drift(_state=tmp_path, runner=anyio.run)
    assert _wait_for_reason(tmp_path, "registration_drift")


def test_session_start_helper_emits_nothing_when_matching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yoetz.cli.hooks import _maybe_record_registration_drift

    record_applied_route(
        "policy",
        list(MCP_SERVE_COMMAND),
        list(MCP_SERVE_COMMAND),
        _DIGEST,
        _state=tmp_path,
    )
    _stub_hook_observation(monkeypatch, registered="policy")

    import anyio

    _maybe_record_registration_drift(_state=tmp_path, runner=anyio.run)
    assert _reasons_in(tmp_path) == []


def test_session_start_helper_emits_nothing_without_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yoetz.cli.hooks import _maybe_record_registration_drift

    # No applied record: the probe must return before touching discovery, so
    # even a broken discovery must not produce a diagnostic or raise.
    def _explode() -> tuple[object, ...]:
        raise AssertionError("must not discover without a record")

    monkeypatch.setattr(
        "yoetz.adapters.integrations.codex_discovery.discover_codex_binaries", _explode
    )

    import anyio

    _maybe_record_registration_drift(_state=tmp_path, runner=anyio.run)
    assert _reasons_in(tmp_path) == []


def test_session_start_helper_emits_nothing_on_observation_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from yoetz.cli.hooks import _maybe_record_registration_drift

    record_applied_route(
        "policy",
        list(MCP_SERVE_COMMAND),
        list(MCP_SERVE_COMMAND),
        _DIGEST,
        _state=tmp_path,
    )
    _stub_hook_observation(monkeypatch, registered="strict", fail=True)

    import anyio

    _maybe_record_registration_drift(_state=tmp_path, runner=anyio.run)
    assert _reasons_in(tmp_path) == []


def test_observe_session_start_emits_drift_and_keeps_return_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The auto-attach SessionStart path emits drift without changing output."""

    from yoetz.cli.observe_hooks import handle_observe

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    record_applied_route(
        "policy",
        list(MCP_SERVE_COMMAND),
        list(MCP_SERVE_COMMAND),
        _DIGEST,
        _state=tmp_path,
    )
    _stub_hook_observation(monkeypatch, registered="strict")

    stdout = io.BytesIO()
    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "drift-session", "hook_event_name": "SessionStart", "cwd": "."}
        ).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    # Output shape is unchanged: exactly one JSON object on stdout.
    json.loads(stdout.getvalue().decode("utf-8"))
    assert _wait_for_reason(tmp_path, "registration_drift")
