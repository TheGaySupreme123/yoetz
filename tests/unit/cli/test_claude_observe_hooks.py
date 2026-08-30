from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.codex_lifecycle import load_mapping
from yoetz.adapters.integrations.observation_local import AdviceDelivery, LocalObservationStore
from yoetz.cli import observe_hooks
from yoetz.domain.observation import (
    AdviceSnapshot,
    ObservationControlCommand,
    ObservationRevokeCommand,
    ObservationSource,
)
from yoetz.kernel.policies.observation_advice import ObservationCompositionFact
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse


def test_claude_hook_ingress_retains_only_closed_structural_mcp_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "cwd": "/private/project",
        "hook_event_name": "PostToolUse",
        "permission_mode": "bypassPermissions",
        "session_id": "session-1",
        "tool_input": {"secret": "private prompt"},
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_response": {"content": "private output"},
        "tool_use_id": "tool-1",
        "transcript_path": "/private/transcript.jsonl",
    }

    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
            workspace=".",
        )
        == 0
    )
    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    assert sanitized == {
        "action": "claude_mcp_success",
        "capability_profile_id": "untested",
        "hook_event_name": "PostToolUse",
        "session_id": "claude:session-1",
        "success": True,
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_use_id": "tool-1",
    }
    assert captured["source"] is ObservationSource.CLAUDE_HOOK


@pytest.mark.parametrize("response_shape", ["structured", "content_blocks"])
def test_claude_successful_start_binds_only_structural_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response_shape: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    start_result: dict[str, JsonValue] = {
        "ok": True,
        "task_id": "tsk_11111111-1111-4111-8111-111111111111",
        "session_id": "ses_22222222-2222-4222-8222-222222222222",
        "writer_id": "wri_33333333-3333-4333-8333-333333333333",
        "frontier": {"sequence": "4", "head_digest": "sha256:" + "a" * 64},
    }
    tool_response: JsonValue = (
        {"structuredContent": start_result}
        if response_shape == "structured"
        else [{"type": "text", "text": canonical_encode(start_result).decode("utf-8")}]
    )
    payload: dict[str, JsonValue] = {
        "hook_event_name": "PostToolUse",
        "session_id": "session-bind",
        "tool_name": "mcp__plugin_yoetz_yoetz__start",
        "tool_response": tool_response,
        "tool_use_id": "tool-bind",
    }

    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
            workspace=".",
            _state=tmp_path,
        )
        == 0
    )
    mapping = load_mapping("claude:session-bind", _state=tmp_path)
    assert mapping is not None
    assert mapping.yoetz_task_id == start_result["task_id"]
    assert mapping.yoetz_session_id == start_result["session_id"]
    assert mapping.yoetz_writer_id == start_result["writer_id"]
    assert mapping.last_frontier == "4:sha256:" + "a" * 64

    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    assert "tool_response" not in sanitized
    assert "task_id" not in sanitized
    assert "writer_id" not in sanitized


def test_claude_failed_or_non_start_result_creates_no_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_handle_observe(**_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for index, (tool_name, response) in enumerate(
        (
            ("mcp__plugin_yoetz_yoetz__start", {"structuredContent": {"ok": False}}),
            (
                "mcp__plugin_yoetz_yoetz__status",
                {
                    "structuredContent": {
                        "ok": True,
                        "task_id": "tsk_11111111-1111-4111-8111-111111111111",
                        "session_id": "ses_22222222-2222-4222-8222-222222222222",
                        "writer_id": "wri_33333333-3333-4333-8333-333333333333",
                    }
                },
            ),
        )
    ):
        session = f"session-negative-{index}"
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(
                {
                    "hook_event_name": "PostToolUse",
                    "session_id": session,
                    "tool_name": tool_name,
                    "tool_response": response,
                }
            ),
            stdout=io.BytesIO(),
            _state=tmp_path,
        )
        assert load_mapping(f"claude:{session}", _state=tmp_path) is None


def test_claude_capability_profile_requires_exact_evidenced_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for version, expected in (
        ("2.1.241", "claude-code-cli-local-project-2.1.241"),
        # A neighboring version whose contract was never proven, and a payload
        # naming no version at all, must both stay explicitly untested rather
        # than emit evidence for the 2.1.241 profile.
        ("2.1.240", "untested"),
        (None, "untested"),
    ):
        payload: dict[str, JsonValue] = {
            "hook_event_name": "Stop",
            "session_id": "session-version",
        }
        if version is not None:
            payload["claude_code_version"] = version
        assert (
            observe_hooks.handle_claude_observe(
                event_name="Stop",
                stdin_bytes=canonical_encode(payload),
                stdout=io.BytesIO(),
            )
            == 0
        )
        assert captured[-1]["capability_profile_id"] == expected


def test_claude_read_guidance_calls_survive_the_scoped_ingress_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "hook_event_name": "PostToolUse",
        "session_id": "session-guidance",
        "tool_name": "mcp__plugin_yoetz_yoetz__read_guidance",
        "tool_use_id": "tool-guidance",
    }
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
        )
        == 0
    )
    assert captured[0]["tool_name"] == "mcp__plugin_yoetz_yoetz__read_guidance"


def test_claude_failure_discards_raw_error_and_bare_mcp_names_are_negative_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    failure: dict[str, JsonValue] = {
        "error": "secret exception",
        "hook_event_name": "PostToolUseFailure",
        "session_id": "session-2",
        "tool_input": {"prompt": "private"},
        "tool_name": "mcp__plugin_yoetz_yoetz__publish_work",
        "tool_use_id": "tool-2",
    }
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUseFailure",
            stdin_bytes=canonical_encode(failure),
            stdout=io.BytesIO(),
        )
        == 0
    )
    assert captured[0]["hook_event_name"] == "PostToolUse"
    assert captured[0]["success"] is False
    assert "error" not in captured[0]

    bare = {**failure, "tool_name": "mcp__yoetz__publish_work"}
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUseFailure",
            stdin_bytes=canonical_encode(cast(JsonValue, bare)),
            stdout=io.BytesIO(),
        )
        == 0
    )
    assert len(captured) == 1


def test_claude_session_source_is_closed_and_content_never_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for source in ("resume", "attacker-controlled"):
        payload: dict[str, JsonValue] = {
            "cwd": "/private/project",
            "hook_event_name": "SessionStart",
            "session_id": f"session-{source}",
            "source": source,
            "transcript_path": "/private/transcript.jsonl",
        }
        observe_hooks.handle_claude_observe(
            event_name="SessionStart",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
        )

    assert captured[0]["action"] == "claude_session_resume"
    assert captured[1]["action"] == "claude_session"
    for item in captured:
        assert "cwd" not in item
        assert "source" not in item
        assert "transcript_path" not in item


def test_claude_stop_retains_only_the_boolean_loop_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        value = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(value, Mapping)
        captured.append(cast(Mapping[str, JsonValue], value))
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    for value in (True, False, "true"):
        observe_hooks.handle_claude_observe(
            event_name="Stop",
            stdin_bytes=canonical_encode(
                {
                    "hook_event_name": "Stop",
                    "session_id": "session-stop",
                    "stop_hook_active": value,
                }
            ),
            stdout=io.BytesIO(),
        )

    assert captured[0]["stop_hook_active"] is True
    assert "stop_hook_active" not in captured[1]
    assert "stop_hook_active" not in captured[2]


def _consented_store(tmp_path: Path) -> tuple[LocalObservationStore, str]:
    store = LocalObservationStore(_state=tmp_path)
    commitment = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(commitment)
    return store, commitment


def _recorded_diagnostics(tmp_path: Path) -> list[tuple[str, str]]:
    path = tmp_path / "observation/hook-diagnostics.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [(row["reason"], row["event"]) for row in rows if "reason" in row]


def test_claude_real_ingress_session_start_emits_native_context_and_privacy_canaries(
    tmp_path: Path,
) -> None:
    """The unstubbed ingress stores one structural envelope and answers in Claude's contract."""

    store, commitment = _consented_store(tmp_path)
    stdout = io.BytesIO()
    canaries: dict[str, JsonValue] = {
        "cwd": "/private/CWD_CANARY",
        "transcript_path": "/private/TRANSCRIPT_CANARY.jsonl",
        "prompt": "PROMPT_CANARY",
        "tool_input": {"command": "INPUT_CANARY"},
        "tool_response": "RESPONSE_CANARY",
    }
    payload: dict[str, JsonValue] = {
        "session_id": "claude-real-start",
        "hook_event_name": "SessionStart",
        "source": "startup",
        "claude_code_version": "2.1.241",
        **canaries,
    }
    assert (
        observe_hooks.handle_claude_observe(
            event_name="SessionStart",
            stdin_bytes=canonical_encode(payload),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    emitted = cast(Mapping[str, JsonValue], strict_json_parse(stdout.getvalue()))
    assert set(emitted) == {"hookSpecificOutput"}
    specific = cast(Mapping[str, JsonValue], emitted["hookSpecificOutput"])
    assert specific["hookEventName"] == "SessionStart"
    assert "no ledger task is mapped yet" in cast(str, specific["additionalContext"])

    envelopes = store.list_envelopes(commitment)
    assert len(envelopes) == 1
    assert envelopes[0].source is ObservationSource.CLAUDE_HOOK
    structural = envelopes[0].structural_payload
    assert structural["capability_profile_id"] == "claude-code-cli-local-project-2.1.241"
    assert structural["hook_name"] == "SessionStart"
    state_bytes = b"".join(
        path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    for canary in (
        "CWD_CANARY",
        "TRANSCRIPT_CANARY",
        "PROMPT_CANARY",
        "INPUT_CANARY",
        "RESPONSE_CANARY",
    ):
        assert canary.encode() not in canonical_encode(structural)
        assert canary.encode() not in state_bytes
        assert canary.encode() not in stdout.getvalue()
    assert _recorded_diagnostics(tmp_path) == []


def test_claude_failure_advice_preserves_raw_event_and_commits_after_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host sees PostToolUseFailure even though Yoetz uses PostToolUse cadence internally."""

    store, commitment = _consented_store(tmp_path)
    advice = AdviceDelivery(
        snapshot=cast(AdviceSnapshot, object()),
        item=None,
        delivery_identity="failure-advice-1",
        text="Review the failed Yoetz operation before continuing.",
    )
    commits: list[str] = []

    def fake_peek(
        self: LocalObservationStore,
        workspace: str,
        *,
        yoetz_session_id: str | None = None,
        allow_standing: bool = True,
        session_commitment: str | None = None,
    ) -> AdviceDelivery:
        del self, workspace, yoetz_session_id, allow_standing, session_commitment
        return advice

    def fake_commit(
        self: LocalObservationStore,
        workspace: str,
        identity: str,
        *,
        yoetz_session_id: str | None = None,
        session_commitment: str | None = None,
    ) -> None:
        del self, workspace, yoetz_session_id, session_commitment
        commits.append(identity)

    monkeypatch.setattr(LocalObservationStore, "peek_advice_for_delivery", fake_peek)
    monkeypatch.setattr(LocalObservationStore, "commit_advice_delivery", fake_commit)
    stdout = io.BytesIO()
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUseFailure",
            stdin_bytes=canonical_encode(
                {
                    "hook_event_name": "PostToolUseFailure",
                    "session_id": "claude-failure-advice",
                    "tool_name": "mcp__plugin_yoetz_yoetz__status",
                    "tool_use_id": "tool-failure-advice",
                }
            ),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    emitted = cast(Mapping[str, JsonValue], strict_json_parse(stdout.getvalue()))
    specific = cast(Mapping[str, JsonValue], emitted["hookSpecificOutput"])
    assert specific == {
        "hookEventName": "PostToolUseFailure",
        "additionalContext": "Review the failed Yoetz operation before continuing.",
    }
    assert commits == ["failure-advice-1"]
    envelope = store.list_envelopes(commitment)[0]
    assert envelope.structural_payload["hook_name"] == "PostToolUse"
    assert envelope.structural_payload["success"] is False


def test_claude_stop_delivers_advice_as_non_error_feedback_not_a_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pending advice at Claude Stop uses the documented additionalContext channel (#420)."""

    store, commitment = _consented_store(tmp_path)
    # SessionStart would deliver any advice pending at that moment together
    # with the attach advisory, so the not-ready provider fact only appears
    # after the session is bound: the Stop pass is then the first delivery.
    compositions = [
        ObservationCompositionFact(
            semantic_configured=False,
            semantic_ready=False,
            provider_factory_ids=(),
            connected_provider_ids=(),
        )
    ]
    original_refresh = LocalObservationStore.refresh_advice

    def refresh_with_composition(
        self: LocalObservationStore, workspace: str, **kwargs: object
    ) -> object:
        kwargs.setdefault("composition", compositions[-1])
        return original_refresh(self, workspace, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(LocalObservationStore, "refresh_advice", refresh_with_composition)
    session = "claude-stop-advice"
    start_out = io.BytesIO()
    assert (
        observe_hooks.handle_claude_observe(
            event_name="SessionStart",
            stdin_bytes=canonical_encode(
                {"session_id": session, "hook_event_name": "SessionStart", "source": "startup"}
            ),
            stdout=start_out,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    session_commitment = store.session_commitment(f"claude:{session}")
    assert store.peek_advice_for_delivery(commitment, session_commitment=session_commitment) is None
    compositions.append(
        ObservationCompositionFact(
            semantic_configured=True,
            semantic_ready=False,
            provider_factory_ids=("fireworks",),
            connected_provider_ids=(),
        )
    )
    store.refresh_advice(commitment)
    assert (
        store.peek_advice_for_delivery(
            commitment, allow_standing=True, session_commitment=session_commitment
        )
        is not None
    )

    stop_out = io.BytesIO()
    assert (
        observe_hooks.handle_claude_observe(
            event_name="Stop",
            stdin_bytes=canonical_encode(
                {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": False}
            ),
            stdout=stop_out,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    emitted = cast(Mapping[str, JsonValue], strict_json_parse(stop_out.getvalue()))
    assert set(emitted) == {"hookSpecificOutput"}
    specific = cast(Mapping[str, JsonValue], emitted["hookSpecificOutput"])
    assert specific["hookEventName"] == "Stop"
    assert "connect_provider" in cast(str, specific["additionalContext"])
    assert b"decision" not in stop_out.getvalue()
    assert store.peek_advice_for_delivery(commitment, session_commitment=session_commitment) is None

    # A second Stop in the same host loop is the documented loop guard: nothing
    # is re-delivered and the output stays empty.
    guarded_out = io.BytesIO()
    assert (
        observe_hooks.handle_claude_observe(
            event_name="Stop",
            stdin_bytes=canonical_encode(
                {"session_id": session, "hook_event_name": "Stop", "stop_hook_active": True}
            ),
            stdout=guarded_out,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert guarded_out.getvalue() == b"{}\n"


@pytest.mark.parametrize(
    ("workspace", "expected"),
    [
        pytest.param("", "workspace_unresolvable", id="empty-locator-from-unset-project-dir"),
        pytest.param("/private/does-not-exist/CANARY", "workspace_unresolvable", id="missing"),
        pytest.param(None, "workspace_unconsented", id="unconsented-explicit"),
    ],
)
def test_claude_ingress_records_a_typed_diagnostic_when_nothing_is_ingested(
    tmp_path: Path, workspace: str | None, expected: str
) -> None:
    """A fail-open `{}` still leaves a payload-free trace naming the dropped layer (#435)."""

    store = LocalObservationStore(_state=tmp_path)
    locator = str(tmp_path) if workspace is None else workspace
    stdout = io.BytesIO()
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PostToolUse",
            stdin_bytes=canonical_encode(
                {
                    "session_id": "claude-dropped",
                    "hook_event_name": "PostToolUse",
                    "tool_name": "mcp__plugin_yoetz_yoetz__status",
                    "cwd": "/private/CWD_CANARY",
                }
            ),
            stdout=stdout,
            workspace=locator,
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert stdout.getvalue() == b"{}\n"
    assert _recorded_diagnostics(tmp_path) == [(expected, "PostToolUse")]
    assert store.list_envelopes(store.workspace_commitment(str(tmp_path.resolve()))) == ()
    diagnostics_bytes = (tmp_path / "observation/hook-diagnostics.jsonl").read_bytes()
    for canary in (b"CANARY", b"does-not-exist", str(tmp_path).encode()):
        assert canary not in diagnostics_bytes


def test_claude_ingress_names_paused_consent_distinctly(tmp_path: Path) -> None:
    store, commitment = _consented_store(tmp_path)
    store.pause(ObservationControlCommand(commitment))
    stdout = io.BytesIO()
    assert (
        observe_hooks.handle_claude_observe(
            event_name="SessionStart",
            stdin_bytes=canonical_encode(
                {"session_id": "claude-paused", "hook_event_name": "SessionStart"}
            ),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert stdout.getvalue() == b"{}\n"
    assert _recorded_diagnostics(tmp_path) == [("paused", "SessionStart")]

    # Revocation keeps the pause flag in the stored record; the diagnostic must
    # still say unconsented, as the `observe status` consent label does.
    store.revoke(ObservationRevokeCommand(commitment, retain_evidence=True))
    assert (
        observe_hooks.handle_claude_observe(
            event_name="SessionStart",
            stdin_bytes=canonical_encode(
                {"session_id": "claude-revoked", "hook_event_name": "SessionStart"}
            ),
            stdout=io.BytesIO(),
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert _recorded_diagnostics(tmp_path)[-1] == ("workspace_unconsented", "SessionStart")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("auto_mode", "host_auto_review_denied"),
        (None, "host_auto_review_denied"),
        ("permission_rule", "host_permission_rule_denied"),
        ("hook", "host_permission_rule_denied"),
    ],
)
def test_claude_permission_denied_on_a_scoped_check_records_one_payload_free_diagnostic(
    tmp_path: Path, source: str | None, expected: str
) -> None:
    """A host reviewer held the check before Yoetz saw it (issue #467).

    The typed reason is the separate representation #187 asked for: it is host tool-call
    authorization, never a semantic status, and nothing from the host payload survives.
    """

    store, _commitment = _consented_store(tmp_path)
    payload: dict[str, object] = {
        "session_id": "claude-denied",
        "hook_event_name": "PermissionDenied",
        "tool_name": "mcp__plugin_yoetz_yoetz__check",
        "tool_input": {"claim": "CLAIM_CANARY"},
        "tool_use_id": "toolu_CANARY",
        "reason": "classifier_denied",
        "cwd": "/private/CWD_CANARY",
        "permission_mode": "auto",
    }
    if source is not None:
        payload["source"] = source
    stdout = io.BytesIO()
    assert (
        observe_hooks.handle_claude_observe(
            event_name="PermissionDenied",
            stdin_bytes=canonical_encode(cast(JsonValue, payload)),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert stdout.getvalue() == b"{}\n"
    assert _recorded_diagnostics(tmp_path) == [(expected, "PermissionDenied")]
    assert store.list_envelopes(store.workspace_commitment(str(tmp_path.resolve()))) == ()
    diagnostics_bytes = (tmp_path / "observation/hook-diagnostics.jsonl").read_bytes()
    for canary in (b"CANARY", b"classifier_denied", str(tmp_path).encode()):
        assert canary not in diagnostics_bytes


def test_claude_permission_denied_for_any_other_tool_records_nothing(tmp_path: Path) -> None:
    _consented_store(tmp_path)
    for tool_name in ("mcp__plugin_yoetz_yoetz__start", "Bash", "mcp__yoetz__check"):
        stdout = io.BytesIO()
        assert (
            observe_hooks.handle_claude_observe(
                event_name="PermissionDenied",
                stdin_bytes=canonical_encode(
                    {
                        "session_id": "claude-denied",
                        "hook_event_name": "PermissionDenied",
                        "tool_name": tool_name,
                        "source": "auto_mode",
                    }
                ),
                stdout=stdout,
                workspace=str(tmp_path),
                _state=tmp_path,
                skip_service=True,
            )
            == 0
        )
        assert stdout.getvalue() == b"{}\n"
    assert not (tmp_path / "observation/hook-diagnostics.jsonl").exists()
