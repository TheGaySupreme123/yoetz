"""Successor acceptance scenarios for observation routing, supervisor, and session advice."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest

from yoetz.adapters.integrations.codex_plugin import render_plugin_tree
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.observation_verification import (
    ObservationVerificationSupervisor,
    ObservationVerificationWorker,
    VerificationDrainHandle,
)
from yoetz.cli.observe_hooks import handle_observe
from yoetz.domain.values import Timestamp


def test_rendered_hooks_declare_workspace_binding_and_three_second_budget() -> None:
    tree = render_plugin_tree()
    hooks = json.loads(tree["hooks/hooks.json"].decode("utf-8"))
    observe_commands: list[dict[str, Any]] = []
    for groups in hooks["hooks"].values():
        for group in groups:
            entries = group.get("hooks", [group] if "command" in group else [])
            for hook in entries:
                command = hook.get("command")
                if isinstance(command, str) and "hooks observe" in command:
                    observe_commands.append(hook)
    assert observe_commands
    for hook in observe_commands:
        command = hook["command"]
        assert isinstance(command, str)
        assert "--workspace ." in command
        assert int(hook["timeout"]) <= 3


def test_two_consented_workspaces_bind_independent_codex_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "state"
    project_a = tmp_path / "proj-a"
    project_b = tmp_path / "proj-b"
    project_a.mkdir()
    project_b.mkdir()
    store = LocalObservationStore(_state=state)
    commit_a = store.workspace_commitment(str(project_a.resolve()))
    commit_b = store.workspace_commitment(str(project_b.resolve()))
    store.grant_consent(commit_a)
    store.grant_consent(commit_b)
    assert commit_a != commit_b

    def _observe(workspace: Path, session: str) -> None:
        payload = json.dumps({"session_id": session, "hook_event_name": "SessionStart"}).encode(
            "utf-8"
        )
        code = handle_observe(
            event_name="SessionStart",
            stdin_bytes=payload,
            workspace=str(workspace),
            _state=state,
            skip_service=True,
            stdout=io.BytesIO(),
        )
        assert code == 0

    _observe(project_a, "codex-session-a")
    _observe(project_b, "codex-session-b")
    assert store.find_workspace_for_codex_session("codex-session-a") == commit_a
    assert store.find_workspace_for_codex_session("codex-session-b") == commit_b
    assert store.find_workspace_for_codex_session("codex-session-a") != commit_b


@pytest.mark.anyio
async def test_supervisor_drains_after_notify_without_inline_await() -> None:
    ran = {"count": 0}

    class _Repo:
        def enqueue_latest(self, **kwargs: object) -> tuple[str, ...]:
            del kwargs
            return ("job-1",)

        def claim_next(self, **kwargs: object) -> object | None:
            del kwargs
            return None

        def complete(self, **kwargs: object) -> None:
            del kwargs

    class _Runner:
        def __init__(self) -> None:
            self._output_sink = None

    worker = ObservationVerificationWorker(
        repository=_Repo(),  # type: ignore[arg-type]
        runner=_Runner(),  # type: ignore[arg-type]
        workspace_provider=lambda _w: object(),  # type: ignore[arg-type,return-value]
        policy_provider=lambda _w, _d: (),
        capture_subject_state=lambda _h: "sha256:" + "d" * 64,
        persist_output=lambda _job, _content: asyncio.sleep(0, result=None),
        service_generation=1,
        lease_owner="svc",
        now=lambda: Timestamp("2026-07-24T00:00:00.000Z").wire,
        lease_expires_at=lambda: Timestamp("2026-07-24T00:02:00.000Z").wire,
    )

    async def _run_once() -> object | None:
        ran["count"] += 1
        return None

    worker.run_once = _run_once  # type: ignore[method-assign]
    supervisor = ObservationVerificationSupervisor(service_generation=1)
    await supervisor.start()
    supervisor.register(
        VerificationDrainHandle(workspace_commitment="hmac-sha256:" + "a" * 64, worker=worker)
    )
    supervisor.notify()
    await asyncio.sleep(0.05)
    assert ran["count"] >= 1
    await supervisor.stop()


def test_untrusted_workspace_dot_does_not_bind_without_consent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "obs-state"
    store = LocalObservationStore(_state=state)
    payload = json.dumps({"session_id": "fresh-session", "hook_event_name": "SessionStart"}).encode(
        "utf-8"
    )
    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=payload,
        workspace=".",
        _state=state,
        skip_service=True,
        stdout=io.BytesIO(),
    )
    assert code == 0
    assert store.find_workspace_for_codex_session("fresh-session") is None


def test_sqlite_session_advice_is_isolated_and_latest_anywhere_is_gone() -> None:
    """Migration-0004 session advice must not leak across Yoetz sessions."""

    import apsw

    from yoetz.adapters.sqlite.migrations import initialize_bundle
    from yoetz.adapters.sqlite.observation import SqliteObservationStore
    from yoetz.domain.findings import FindingId, finding_id
    from yoetz.domain.observation import AdviceSnapshot
    from yoetz.protocol.coverage import (
        ArtifactObservation,
        AuthorshipAssurance,
        CheckType,
        Coverage,
        EvidenceImmutability,
        LedgerFreshness,
        PublicationChannel,
    )
    from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_session_advice", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    workspace = "hmac-sha256:" + "c" * 64
    now = Timestamp("2026-07-24T00:00:00.000Z")
    coverage = Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
        evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
        ledger_freshness=LedgerFreshness.CURRENT,
        check_types=(CheckType.DETERMINISTIC,),
        known_gaps=(),
    )
    finding: FindingId = finding_id(
        PREFIX_BY_KIND[IdKind.FINDING] + "00000000-0000-4000-8000-000000000001"
    )

    def _snap(token: str) -> AdviceSnapshot:
        return AdviceSnapshot(
            ranked_finding_ids=(finding,),
            ranked_items=(),
            recommended_next_action="reground_status",
            evidence_basis_digest="sha256:" + (token * 64)[:64],
            confidence_coverage=coverage,
            freshness_frontier=f"frontier-{token}",
            suppression_identity=f"suppress-{token}",
        )

    store.set_session_advice_snapshot(
        workspace=workspace,
        yoetz_session_id="session-a",
        snapshot=_snap("a"),
        updated_at=now,
    )
    store.set_session_advice_snapshot(
        workspace=workspace,
        yoetz_session_id="session-b",
        snapshot=_snap("b"),
        updated_at=now,
    )
    a = store.load_advice_snapshot_for_session(workspace=workspace, yoetz_session_id="session-a")
    b = store.load_advice_snapshot_for_session(workspace=workspace, yoetz_session_id="session-b")
    assert a is not None and b is not None
    assert a.suppression_identity != b.suppression_identity
    assert store.load_latest_advice_snapshot() is None


def test_unknown_hook_event_becomes_unsupported_without_guessing_success() -> None:
    from yoetz.cli.observe_hooks import map_hook_payload_to_envelope
    from yoetz.domain.observation import ObservationGapCode
    from yoetz.domain.values import JsonObject

    payload = JsonObject(
        {
            "session_id": "future-session",
            "hook_event_name": "FutureCodexEvent_v9",
            "visible_text": "hello-future",
        }
    )
    envelope = map_hook_payload_to_envelope(
        event_name="FutureCodexEvent_v9",
        payload=payload,
        session_commitment="hmac-sha256:" + "a" * 64,
        event_ordinal=1,
        key_material=b"k" * 32,
    )
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes
    assert envelope.structural_payload.get("hook_name") == "unsupported"
