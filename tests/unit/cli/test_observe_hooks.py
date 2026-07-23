"""Unit tests for unified hook observation ingress and consent plumbing."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli import observe_hooks as observe_hooks_module
from yoetz.cli.observe_hooks import (
    SUPPORTED_HOOK_EVENTS,
    handle_observe,
    map_hook_payload_to_envelope,
)
from yoetz.domain.observation import (
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
    ObservationStatusQuery,
)

_KEY = b"k" * 32


def test_map_supported_hook_payloads_structural_only(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-1")
    for event in sorted(SUPPORTED_HOOK_EVENTS):
        payload = {
            "session_id": "sess-1",
            "hook_event_name": event,
            "tool_name": "shell",
            "correlation_id": "corr-1",
            "exit_status": 0,
            "transcript": "MUST_NOT_APPEAR",
            "prompt": "MUST_NOT_APPEAR",
        }
        envelope = map_hook_payload_to_envelope(
            event,
            payload,
            session_commitment=session,
            event_ordinal=1,
            key_material=store.key_material(),
        )
        assert envelope.source is ObservationSource.CODEX_HOOK
        assert envelope.event_kind == event
        assert "transcript" not in envelope.structural_payload
        assert "prompt" not in envelope.structural_payload
        assert envelope.structural_payload.get("tool_name") == "shell"
        assert envelope.cursor.last_source_commitment.startswith("hmac-sha256:")


def test_unknown_future_hook_becomes_opaque_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-2")
    envelope = map_hook_payload_to_envelope(
        "FutureHostEvent",
        {"session_id": "sess-2"},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
        gap_codes=(ObservationGapCode.UNSUPPORTED_EVENT.value,),
    )
    assert envelope.event_kind == "FutureHostEvent"
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes


def test_visible_unknown_content_is_redacted_and_hidden_fields_are_ignored(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-visible")
    envelope = map_hook_payload_to_envelope(
        "FutureHostEvent",
        {"session_id": "sess-visible"},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    chunks = observe_hooks_module._visible_content_chunks(  # pyright: ignore[reportPrivateUsage]
        "FutureHostEvent",
        {
            "visibility": "task",
            "visible_content": "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
            "reasoning": "HIDDEN_REASONING_CANARY",
            "system": "SYSTEM_PROMPT_CANARY",
        },
        envelope=envelope,
        workspace_locator=None,
    )
    assert len(chunks) == 1
    assert chunks[0].redacted is True
    assert b"sk-abcdefghijklmnopqrstuvwxyz123456" not in chunks[0].content
    assert b"HIDDEN_REASONING_CANARY" not in chunks[0].content
    assert b"SYSTEM_PROMPT_CANARY" not in chunks[0].content


def test_identical_tool_calls_remain_distinct(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-dup")
    payload = {
        "session_id": "sess-dup",
        "tool_name": "shell",
        "tool_call_id": "same-call",
        "exit_status": 0,
    }
    first = map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    second = map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=session,
        event_ordinal=2,
        key_material=_KEY,
    )
    assert first.source_identity != second.source_identity


def test_observe_without_consent_exits_zero_no_spool(tmp_path: Path) -> None:
    stdout = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "s1", "tool_name": "shell", "exit_status": 0}
        ).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert json.loads(stdout.getvalue().decode()) == {}


def test_service_unavailable_never_spools_visible_plaintext(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    canary = "VISIBLE_TASK_CANARY_6f4b2f"
    code = handle_observe(
        event_name="UserPromptSubmit",
        stdin_bytes=json.dumps(
            {"session_id": "no-spool", "prompt": canary, "event_ordinal": 1}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    persisted = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert canary.encode() not in persisted
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value in status.gaps


def test_observe_ingests_when_consented_and_pairs_pre_post(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    pre = handle_observe(
        event_name="PreToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-1",
                "tool_name": "shell",
                "correlation_id": "c1",
                "tool_call_id": "c1",
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert pre == 0
    post_out = io.BytesIO()
    post = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-1",
                "tool_name": "shell",
                "correlation_id": "c1",
                "tool_call_id": "c1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=post_out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert post == 0
    status = store.status(
        __import__(
            "yoetz.domain.observation", fromlist=["ObservationStatusQuery"]
        ).ObservationStatusQuery(workspace)
    )
    assert status.source_coverage[ObservationSource.CODEX_HOOK] is True
    assert ObservationGapCode.UNPAIRED_EVENT.value not in status.gaps


def test_unpaired_post_records_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-2",
                "tool_name": "shell",
                "correlation_id": "missing-pre",
                "exit_status": 1,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    status = store.status(
        __import__(
            "yoetz.domain.observation", fromlist=["ObservationStatusQuery"]
        ).ObservationStatusQuery(workspace)
    )
    assert ObservationGapCode.UNPAIRED_EVENT.value in status.gaps


def test_yoetz_tool_still_ingests_but_skips_advice_loop(tmp_path: Path) -> None:
    from yoetz.domain.observation import AdviceSnapshot
    from yoetz.domain.values import finding_id
    from yoetz.protocol.coverage import (
        ArtifactObservation,
        AuthorshipAssurance,
        CheckType,
        Coverage,
        EvidenceImmutability,
        LedgerFreshness,
        PublicationChannel,
    )

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.set_advice_snapshot(
        workspace,
        AdviceSnapshot(
            ranked_finding_ids=(finding_id("fnd_00000000-0000-4000-8000-000000000001"),),
            evidence_basis_digest="sha256:" + "a" * 64,
            confidence_coverage=Coverage(
                publication_channels=(PublicationChannel.HOOK_OBSERVED,),
                authorship_assurance=AuthorshipAssurance.HARNESS_OBSERVED,
                artifact_observation=ArtifactObservation.HOOK_OBSERVED,
                evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
                ledger_freshness=LedgerFreshness.CURRENT,
                check_types=(CheckType.DETERMINISTIC,),
                known_gaps=(),
            ),
            recommended_next_action="call_status",
            freshness_frontier="frontier-1",
            suppression_identity="suppress-1",
        ),
    )
    out = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "yoetz-tool", "tool_name": "mcp__yoetz__status"}
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert json.loads(out.getvalue().decode()) == {}


def test_malformed_stdin_exits_zero(tmp_path: Path) -> None:
    code = handle_observe(
        event_name="Stop",
        stdin_bytes=b"{not-json",
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0


def _drain_envelope(store: LocalObservationStore, session: str, identity: str, ordinal: int):
    payload = {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": "shell",
        "correlation_id": f"corr-{ordinal}",
        "exit_status": 1,
    }
    commitment = store.session_commitment(session)
    return map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=commitment,
        event_ordinal=ordinal,
        key_material=store.key_material(),
    )


@pytest.mark.anyio
async def test_drain_quarantines_permanent_and_keeps_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "sess-drain")
    perm = _drain_envelope(store, "sess-drain", "hook:perm", 1)
    retry = _drain_envelope(store, "sess-drain", "hook:retry", 2)
    store.enqueue_outbox(workspace, "sess-drain", perm)
    store.enqueue_outbox(workspace, "sess-drain", retry)
    assert store.pending_outbox_count(workspace) == 2

    async def _fake_ingest(session_id: str, envelope: ObservationEnvelope):
        if envelope.source_identity == perm.source_identity:
            # Permanently invalid rejection.
            return None, ObservationGapCode.CONSENT_REVOKED.value
        # Retryable rejection.
        return None, ObservationGapCode.SERVICE_UNAVAILABLE.value

    monkeypatch.setattr(observe_hooks_module, "_try_service_ingest", _fake_ingest)

    await observe_hooks_module._drain_outbox(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        store, workspace_commitment=workspace, codex_session_id="sess-drain"
    )

    # Permanent -> quarantined (never dropped); retryable -> still pending.
    assert store.quarantined_count(workspace) == 1
    assert store.list_quarantine(workspace)[0][1].source_identity == perm.source_identity
    pending = store.list_pending_outbox(workspace)
    assert len(pending) == 1
    assert pending[0][1].source_identity == retry.source_identity


@pytest.mark.anyio
async def test_drain_is_round_robin_across_all_workspace_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for session in ("current", "recovered"):
        store.bind_codex_session(workspace, session)
        for ordinal in (1, 2):
            store.enqueue_outbox(
                workspace,
                session,
                _drain_envelope(store, session, f"hook:{session}:{ordinal}", ordinal),
            )
    calls: list[str] = []

    async def _accept(session_id: str, envelope: ObservationEnvelope):
        del envelope
        calls.append(session_id)
        return None, None

    monkeypatch.setattr(observe_hooks_module, "_try_service_ingest", _accept)
    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="current",
    )
    assert calls == ["current", "recovered", "current", "recovered"]
    assert store.pending_outbox_count(workspace) == 0
