from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.recommendations import (
    RecommendationState,
    cached_pending_recommendations,
    store_recommendation_state,
)
from yoetz.cli import observe_hooks
from yoetz.domain.observation import ObservationSource
from yoetz.kernel.policies.observation_advice import ObservationCompositionFact
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse


def _consented_store(tmp_path: Path) -> tuple[LocalObservationStore, str]:
    store = LocalObservationStore(_state=tmp_path)
    commitment = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(commitment)
    return store, commitment


def test_cursor_hook_ingress_drops_every_content_and_identity_denylist_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    payload: dict[str, JsonValue] = {
        "conversation_id": "cursor-session-1",
        "cursor_version": "3.17.8",
        "generation_id": "generation-1",
        "hook_event_name": "afterMCPExecution",
        "model_id": "cursor-grok-4.6-medium",
        "model_params": [{"id": "effort", "value": "medium"}],
        "tool_name": "mcp__yoetz__start",
        "tool_input": "secret prompt and arguments",
        "result_json": "private tool result",
        "transcript_path": "/private/transcript.jsonl",
        "user_email": "private@example.com",
        "workspace_roots": [str(tmp_path)],
    }
    assert (
        observe_hooks.handle_cursor_observe(
            event_name="afterMCPExecution",
            stdin_bytes=canonical_encode(payload),
            workspace=str(tmp_path),
        )
        == 0
    )

    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    assert sanitized["session_id"] == "cursor:cursor-session-1"
    assert sanitized["cursor_version"] == "3.17.8"
    assert sanitized["capability_profile_id"] == "cursor-ide-3.17.8"
    assert sanitized["model_id"] == "cursor-grok-4.6-medium"
    assert sanitized["model_effort"] == "medium"
    assert captured["source"] is ObservationSource.CURSOR_HOOK
    assert captured["_output_event_name"] == "afterMCPExecution"
    assert "result_status" not in sanitized
    assert "success" not in sanitized
    forbidden = {
        "tool_input",
        "result_json",
        "transcript_path",
        "user_email",
        "workspace_roots",
        "prompt",
        "response",
        "file_path",
        "edits",
    }
    assert forbidden.isdisjoint(sanitized)


def test_cursor_file_edit_uses_keyed_path_commitment_and_drops_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_observe(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)
    path = "/private/project/secret.py"
    payload: dict[str, JsonValue] = {
        "conversation_id": "cursor-session-2",
        "hook_event_name": "afterFileEdit",
        "file_path": path,
        "edits": [{"old_string": "secret", "new_string": "private"}],
    }

    assert (
        observe_hooks.handle_cursor_observe(
            event_name="afterFileEdit",
            stdin_bytes=canonical_encode(payload),
            stdout=io.BytesIO(),
            workspace=".",
            _state=tmp_path,
        )
        == 0
    )

    sanitized = strict_json_parse(cast(bytes, captured["stdin_bytes"]))
    assert isinstance(sanitized, Mapping)
    commitment = sanitized["changed_paths_digest"]
    assert isinstance(commitment, str) and commitment.startswith("hmac-sha256:")
    assert path not in canonical_encode(cast(JsonValue, sanitized)).decode("utf-8")
    assert "result_status" not in sanitized
    assert "success" not in sanitized
    assert sanitized["capability_profile_id"] == "untested"


def test_cursor_session_prefix_reserves_space_inside_token_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, JsonValue]] = []

    def fake_handle_observe(**kwargs: object) -> int:
        payload = strict_json_parse(cast(bytes, kwargs["stdin_bytes"]))
        assert isinstance(payload, Mapping)
        captured.append(payload)
        return 0

    monkeypatch.setattr(observe_hooks, "handle_observe", fake_handle_observe)

    for length in (121, 122):
        payload: dict[str, JsonValue] = {
            "conversation_id": "s" * length,
            "hook_event_name": "sessionStart",
        }
        assert (
            observe_hooks.handle_cursor_observe(
                event_name="sessionStart",
                stdin_bytes=canonical_encode(payload),
                stdout=io.BytesIO(),
                workspace=".",
            )
            == 0
        )

    assert len(captured) == 1
    assert captured[0]["session_id"] == "cursor:" + ("s" * 121)
    assert len(cast(str, captured[0]["session_id"])) == 128


def test_cursor_cached_recommendation_uses_cursor_session_start_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real ingress must emit Cursor's raw event contract, not Codex's envelope."""

    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    store = LocalObservationStore(_state=tmp_path)
    store.set_runtime_enabled(False)
    store_recommendation_state(
        RecommendationState(last_evaluated_version="0.1.0", pending=("observation-enabled",)),
        root=tmp_path,
    )
    stdout = io.BytesIO()

    assert (
        observe_hooks.handle_cursor_observe(
            event_name="sessionStart",
            stdin_bytes=canonical_encode(
                {
                    "conversation_id": "cursor-cache",
                    "hook_event_name": "sessionStart",
                    "cursor_version": "3.17.8",
                }
            ),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
        )
        == 0
    )

    emitted = json.loads(stdout.getvalue())
    assert set(emitted) == {"additional_context"}
    assert "Enable local observation" in emitted["additional_context"]
    assert "hookSpecificOutput" not in emitted

    # A Cursor event without a consumable stdout channel cannot spend the same
    # cached recommendation or manufacture a follow-up message.
    stdout = io.BytesIO()
    assert (
        observe_hooks.handle_cursor_observe(
            event_name="afterMCPExecution",
            stdin_bytes=canonical_encode(
                {
                    "conversation_id": "cursor-cache",
                    "hook_event_name": "afterMCPExecution",
                    "cursor_version": "3.17.8",
                }
            ),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
        )
        == 0
    )
    assert json.loads(stdout.getvalue()) == {}
    assert cached_pending_recommendations(root=tmp_path, limit=1)


@pytest.mark.parametrize(
    ("cursor_version", "profile"),
    [
        ("3.17.8", "cursor-ide-3.17.8"),
        ("2026.07.09-a3815c0", "untested"),
        ("1.0.24", "untested"),
        ("999.0", "untested"),
    ],
)
def test_cursor_real_ingress_uses_bounded_profile_and_privacy_canaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cursor_version: str,
    profile: str,
) -> None:
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(tmp_path))
    store, commitment = _consented_store(tmp_path)
    stdout = io.BytesIO()
    other_root = tmp_path / "ROOT_CANARY"
    other_root.mkdir()
    canaries: dict[str, JsonValue] = {
        "tool_input": "PROMPT_CANARY",
        "result_json": "RESULT_CANARY",
        "transcript_path": "/private/TRANSCRIPT_CANARY",
        "user_email": "EMAIL_CANARY@example.com",
        "file_path": "/private/FILE_CANARY.py",
        "edits": [{"old_string": "OLD_CANARY", "new_string": "NEW_CANARY"}],
        "workspace_roots": [str(tmp_path), str(other_root)],
    }
    payload: dict[str, JsonValue] = {
        "conversation_id": "cursor-real-profile",
        "hook_event_name": "sessionStart",
        "source": "clear",
        "cursor_version": cursor_version,
        **canaries,
    }

    assert (
        observe_hooks.handle_cursor_observe(
            event_name="sessionStart",
            stdin_bytes=canonical_encode(payload),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    emitted = json.loads(stdout.getvalue())
    assert set(emitted) == {"additional_context"}
    assert "no ledger task is mapped yet" in emitted["additional_context"]
    assert stdout.getvalue() == (
        b'{"additional_context":"Yoetz observation is consented for this workspace; '
        b"no ledger task is mapped yet (observation-derived binding only). Call start to attach "
        b'a task."}\n'
    )

    envelopes = store.list_envelopes(commitment)
    assert len(envelopes) == 1
    structural = envelopes[0].structural_payload
    assert structural["capability_profile_id"] == profile
    assert structural["hook_name"] == "SessionStart"
    stored = canonical_encode(structural)
    state_bytes = b"".join(
        path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    for canary in (
        "PROMPT_CANARY",
        "RESULT_CANARY",
        "TRANSCRIPT_CANARY",
        "EMAIL_CANARY",
        "FILE_CANARY",
        "OLD_CANARY",
        "NEW_CANARY",
        "ROOT_CANARY",
        "workspace_roots",
    ):
        assert canary.encode() not in stored
        assert canary.encode() not in state_bytes


def test_cursor_outputless_event_does_not_lease_or_commit_frontier_motion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    store, commitment = _consented_store(tmp_path)
    session = "cursor:cursor-pending"
    store.bind_codex_session(commitment, session)
    store.note_frontier_motion(
        commitment,
        session,
        from_sequence=1,
        to_sequence=2,
        head_digest="sha256:" + "3" * 64,
        observation_record_count=1,
        task_id="tsk_cursor_pending",
    )
    lease_calls: list[str] = []
    original_lease = LocalObservationStore.advice_delivery_lease

    def tracked_lease(self: LocalObservationStore, workspace: str) -> object:
        lease_calls.append(workspace)
        return original_lease(self, workspace)

    monkeypatch.setattr(LocalObservationStore, "advice_delivery_lease", tracked_lease)
    stdout = io.BytesIO()

    assert (
        observe_hooks.handle_cursor_observe(
            event_name="afterMCPExecution",
            stdin_bytes=canonical_encode(
                {
                    "conversation_id": "cursor-pending",
                    "hook_event_name": "afterMCPExecution",
                    "cursor_version": "3.17.8",
                    "tool_name": "shell",
                }
            ),
            stdout=stdout,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )

    assert json.loads(stdout.getvalue()) == {}
    assert lease_calls == []
    assert store.peek_frontier_motion(commitment, session) is not None


def test_cursor_advice_delivery_stays_pending_until_session_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real AdviceDelivery state survives every Cursor output-less hook."""

    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    store, commitment = _consented_store(tmp_path)
    composition = ObservationCompositionFact(
        semantic_configured=True,
        semantic_ready=False,
        provider_factory_ids=("fireworks",),
        connected_provider_ids=(),
    )
    original_refresh = LocalObservationStore.refresh_advice

    def refresh_with_composition(
        self: LocalObservationStore, workspace: str, **kwargs: object
    ) -> object:
        kwargs.setdefault("composition", composition)
        return original_refresh(self, workspace, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(LocalObservationStore, "refresh_advice", refresh_with_composition)
    lease_calls: list[str] = []
    original_lease = LocalObservationStore.advice_delivery_lease

    def tracked_lease(self: LocalObservationStore, workspace: str) -> object:
        lease_calls.append(workspace)
        return original_lease(self, workspace)

    monkeypatch.setattr(LocalObservationStore, "advice_delivery_lease", tracked_lease)

    session = "cursor-pending-advice"
    session_commitment = store.session_commitment(f"cursor:{session}")
    initial_out = io.BytesIO()
    assert (
        observe_hooks.handle_cursor_observe(
            event_name="sessionStart",
            stdin_bytes=canonical_encode(
                {
                    "conversation_id": session,
                    "hook_event_name": "sessionStart",
                    "cursor_version": "3.17.8",
                    "tool_name": "mcp__yoetz__start",
                    "source": "clear",
                }
            ),
            stdout=initial_out,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    assert json.loads(initial_out.getvalue()) == {}
    assert store.advice_snapshot_for(commitment) is not None
    assert store.peek_advice_for_delivery(commitment) is not None
    assert lease_calls == []

    for event_name, extra in (
        ("afterFileEdit", {"file_path": "/private/cursor-canary.py"}),
        ("afterMCPExecution", {}),
        ("sessionEnd", {}),
        ("stop", {}),
    ):
        output = io.BytesIO()
        assert (
            observe_hooks.handle_cursor_observe(
                event_name=event_name,
                stdin_bytes=canonical_encode(
                    {
                        "conversation_id": session,
                        "hook_event_name": event_name,
                        "cursor_version": "3.17.8",
                        **extra,
                    }
                ),
                stdout=output,
                workspace=str(tmp_path),
                _state=tmp_path,
                skip_service=True,
            )
            == 0
        )
        assert output.getvalue() == b"{}\n"
        assert store.peek_advice_for_delivery(commitment) is not None

    final_out = io.BytesIO()
    assert (
        observe_hooks.handle_cursor_observe(
            event_name="sessionStart",
            stdin_bytes=canonical_encode(
                {
                    "conversation_id": session,
                    "hook_event_name": "sessionStart",
                    "cursor_version": "3.17.8",
                    "source": "clear",
                }
            ),
            stdout=final_out,
            workspace=str(tmp_path),
            _state=tmp_path,
            skip_service=True,
        )
        == 0
    )
    emitted = json.loads(final_out.getvalue())
    assert set(emitted) == {"additional_context"}
    assert "connect_provider" in emitted["additional_context"]
    assert len(lease_calls) == 1
    assert (
        store.peek_advice_for_delivery(
            commitment,
            session_commitment=session_commitment,
        )
        is None
    )


def test_cursor_workspace_diagnostics_distinguish_unconsented_and_unresolvable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CURSOR_PROJECT_DIR", raising=False)
    diagnostics: list[str] = []

    def record(reason: str, event: str, *, _state: Path | None = None) -> None:
        del event, _state
        diagnostics.append(reason)

    monkeypatch.setattr(observe_hooks, "record_hook_diagnostic", record)
    unconsented_out = io.BytesIO()
    assert (
        observe_hooks.handle_cursor_observe(
            event_name="sessionStart",
            stdin_bytes=canonical_encode(
                {"conversation_id": "unconsented", "hook_event_name": "sessionStart"}
            ),
            stdout=unconsented_out,
            workspace=str(tmp_path),
            _state=tmp_path,
        )
        == 0
    )
    assert json.loads(unconsented_out.getvalue()) == {}
    assert diagnostics == ["workspace_unconsented"]

    first = tmp_path / "first"
    second = tmp_path / "second"
    outside = tmp_path / "outside"
    first.mkdir()
    second.mkdir()
    outside.mkdir()
    monkeypatch.setenv("CURSOR_PROJECT_DIR", str(outside))
    unresolved_out = io.BytesIO()
    assert (
        observe_hooks.handle_cursor_observe(
            event_name="sessionStart",
            stdin_bytes=canonical_encode(
                {
                    "conversation_id": "unresolvable",
                    "hook_event_name": "sessionStart",
                    "workspace_roots": [str(first), str(second)],
                }
            ),
            stdout=unresolved_out,
            workspace=str(tmp_path),
            _state=tmp_path,
        )
        == 0
    )
    assert json.loads(unresolved_out.getvalue()) == {}
    assert diagnostics[-1] == "workspace_unresolvable"
