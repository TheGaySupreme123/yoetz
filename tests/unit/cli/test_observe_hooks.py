"""Unit tests for unified hook observation ingress and consent plumbing."""

from __future__ import annotations

import io
import json
from pathlib import Path

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.cli.observe_hooks import (
    SUPPORTED_HOOK_EVENTS,
    handle_observe,
    map_hook_payload_to_envelope,
)
from yoetz.domain.observation import ObservationGapCode, ObservationSource


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
            event, payload, session_commitment=session, event_ordinal=1
        )
        assert envelope.source is ObservationSource.CODEX_HOOK
        assert envelope.event_kind == event
        assert "transcript" not in envelope.structural_payload
        assert "prompt" not in envelope.structural_payload
        assert envelope.structural_payload.get("tool_name") == "shell"


def test_unknown_future_hook_becomes_opaque_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-2")
    envelope = map_hook_payload_to_envelope(
        "FutureHostEvent",
        {"session_id": "sess-2"},
        session_commitment=session,
        event_ordinal=1,
        gap_codes=(ObservationGapCode.UNSUPPORTED_EVENT.value,),
    )
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes


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
