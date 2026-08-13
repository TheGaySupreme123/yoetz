"""Integration-style tests for observation advice construction and wiring."""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Mapping
from pathlib import Path

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.observation_semantic_advice import NullSemanticAdvice, OptionalSemanticAdvice
from yoetz.application.observation_advice import (
    ObservationAdviceBuildInput,
    ObservationAdviceContextBuilder,
    build_observation_advice_snapshot,
    minimized_semantic_evidence_packet,
    should_reissue_advice,
)
from yoetz.application.observation_coordinator import (
    _materialized_advice_items,  # pyright: ignore[reportPrivateUsage]  # noqa: PLC2701
)
from yoetz.cli.observe_hooks import handle_observe
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationLifecycle,
    ObservationSource,
    ObservationStatus,
    ObservationStatusQuery,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.kernel.policies.observation_advice import (
    ObservationAdviceContext,
    ObservationCompositionFact,
    observation_advice_findings,
)

_COMMITMENT = "hmac-sha256:" + "a" * 64
_TIME = Timestamp("2026-07-22T21:00:00.000Z")


def _envelope(identity: str, payload: dict[str, object], *, pos: int = 1) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=_COMMITMENT,
        event_kind="PostToolUse",
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=pos * 8,
            event_position=pos,
            last_source_commitment=_COMMITMENT,
            mapping_version="codex-obs-hook/1.0.0",
        ),
        receipt_time=_TIME,
        structural_payload=JsonObject(payload),
        content_object_refs=(),
        gap_codes=(),
    )


def test_zero_cooperative_publications_still_yields_advice() -> None:
    envelopes = (
        _envelope(
            "hook:fail",
            {"tool_name": "shell", "exit_status": 2, "correlation_id": "x1"},
        ),
    )
    snapshot = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            has_real_observation=True,
        )
    )
    assert snapshot is not None
    assert snapshot.ranked_finding_ids
    assert snapshot.ranked_items
    assert snapshot.ranked_items[0].rule_code == "failed_command_unresolved"
    assert snapshot.ranked_items[0].summary
    assert snapshot.recommended_next_action == "resolve_failed_command"
    assert "SECRET" not in snapshot.recommended_next_action


def test_completion_without_verification_is_clear() -> None:
    envelopes = (
        _envelope(
            "hook:done",
            {"claim_kind": "completion", "result_status": "completed"},
            pos=1,
        ),
    )
    snapshot = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            has_real_observation=True,
        )
    )
    assert snapshot is not None
    assert any(
        item.rule_code == "completion_without_verification"
        and item.summary == "Completion not supported by current evidence"
        for item in snapshot.ranked_items
    )


def test_standing_provider_condition_keeps_one_candidate_identity_as_envelopes_grow() -> None:
    composition = ObservationCompositionFact(
        semantic_configured=True,
        semantic_ready=False,
        provider_factory_ids=("provider-a",),
        connected_provider_ids=(),
    )
    first = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=(_envelope("hook:one", {"tool_name": "shell"}),),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=composition,
            has_real_observation=True,
        )
    )
    many = tuple(
        _envelope(f"hook:{index}", {"tool_name": "shell"}, pos=index) for index in range(1, 21)
    )
    latest = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=many,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=composition,
            has_real_observation=True,
        )
    )
    assert first is not None and latest is not None
    first_provider = next(
        item for item in first.ranked_items if item.rule_code == "provider_not_ready"
    )
    latest_provider = next(
        item for item in latest.ranked_items if item.rule_code == "provider_not_ready"
    )
    assert latest_provider.finding_id == first_provider.finding_id
    assert latest.evidence_basis_digest != first.evidence_basis_digest
    assert latest_provider not in _materialized_advice_items(latest.ranked_items)


def test_semantic_packet_minimization() -> None:
    envelopes = (
        _envelope(
            "hook:fail",
            {"tool_name": "shell", "exit_status": 1, "correlation_id": "x1"},
        ),
    )
    candidates = observation_advice_findings(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=("source_lag",),
        )
    )
    packet = minimized_semantic_evidence_packet(
        candidates,
        "sha256:" + "e" * 64,
        coverage_gaps=("source_lag",),
        finding_summaries=("Unresolved failed command observed",),
    )
    assert "transcript" not in packet
    assert "stdout" not in packet
    assert "path" not in packet
    assert packet["coverage_gaps"] == ("source_lag",)
    serialized = str(packet)
    assert "/Users" not in serialized
    assert "SECRET" not in serialized


def test_suppression_skips_duplicate_until_evidence_changes() -> None:
    envelopes = (
        _envelope(
            "hook:fail",
            {"tool_name": "shell", "exit_status": 1, "correlation_id": "x1"},
        ),
    )
    first = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            has_real_observation=True,
        )
    )
    assert first is not None
    second = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            prior_snapshot=first,
            has_real_observation=True,
        )
    )
    assert second is first
    assert should_reissue_advice(first, first) is False
    changed = (
        envelopes[0],
        _envelope(
            "hook:fail2",
            {"tool_name": "shell", "exit_status": 1, "correlation_id": "x2"},
            pos=2,
        ),
    )
    third = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=changed,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            prior_snapshot=first,
            has_real_observation=True,
        )
    )
    assert third is not None
    assert third.suppression_identity != first.suppression_identity


def test_deterministic_only_vs_configured_semantic() -> None:
    envelopes = (
        _envelope(
            "hook:fail",
            {"tool_name": "shell", "exit_status": 1, "correlation_id": "x1"},
        ),
    )
    candidates = observation_advice_findings(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
        )
    )
    packet = minimized_semantic_evidence_packet(candidates, "sha256:" + "e" * 64)
    assert "transcript" not in packet
    assert "stdout" not in packet
    null = NullSemanticAdvice().review(evidence_packet=packet)
    assert null is None

    def _eval(payload: Mapping[str, object]) -> Mapping[str, object]:
        assert "transcript" not in payload
        return {"detail_token": "sem-1", "next_action": "reground_status"}

    addon = OptionalSemanticAdvice(configured=True, ready=True, evaluator=_eval).review(
        evidence_packet=packet
    )
    assert addon is not None
    snapshot = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            semantic_addon=addon,
            has_real_observation=True,
        )
    )
    assert snapshot is not None
    assert len(snapshot.ranked_finding_ids) >= 2


def test_semantic_observation_review_receives_the_trusted_yoetz_session() -> None:
    session_id = "ses_00000000-0000-4000-8000-000000000001"
    observed: list[str | None] = []

    class _Store:
        def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]:
            assert workspace == _COMMITMENT
            return (
                _envelope(
                    "hook:fail",
                    {"tool_name": "shell", "exit_status": 1, "correlation_id": "x1"},
                ),
            )

        async def status(self, query: ObservationStatusQuery) -> ObservationStatus:
            assert query.workspace_commitment == _COMMITMENT
            return ObservationStatus(
                ObservationLifecycle.ACTIVE,
                _COMMITMENT,
                {},
                _TIME,
                0,
                (),
                (),
                None,
            )

        def load_advice_snapshot(self, workspace: str) -> None:
            assert workspace == _COMMITMENT
            return None

    async def review(
        candidates: object, basis: str, gaps: tuple[str, ...], yoetz_session_id: str | None
    ) -> None:
        del candidates, basis, gaps
        observed.append(yoetz_session_id)

    builder = ObservationAdviceContextBuilder(semantic_review=review)  # type: ignore[arg-type]
    snapshot = asyncio.run(
        builder.build(_COMMITMENT, _Store(), yoetz_session_id=session_id)  # type: ignore[arg-type]
    )

    assert snapshot is not None
    assert observed == [session_id]


def test_observe_hook_refresh_advice_without_mcp_tools(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    out = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "advice-1",
                "tool_name": "shell",
                "correlation_id": "c-fail",
                "exit_status": 1,
            }
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    status = store.status(ObservationStatusQuery(workspace))
    assert status.advice_frontier is not None
    payload = json.loads(out.getvalue().decode() or "{}")
    # Safe-event delivery consumes the snapshot once into additionalContext.
    context = payload.get("hookSpecificOutput") or payload
    serialized = json.dumps(payload)
    assert "resolve_failed_command" in serialized or status.advice_frontier != "none"
    # Second peek is suppressed (same evidence frontier).
    assert store.peek_advice_for_delivery(workspace) is None
    _ = context
    text = serialized
    assert "AKIA" not in text
    assert "password" not in text.lower()


def test_secret_like_command_output_absent_from_advice_surfaces(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "advice-2",
                "tool_name": "shell",
                "exit_status": 1,
                "stdout": "AWS_SECRET=should-never-appear",
                "transcript": "hidden reasoning with password=hunter2",
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    snapshot = store.refresh_advice(
        workspace,
        composition=ObservationCompositionFact(
            semantic_configured=False,
            semantic_ready=False,
            provider_factory_ids=(),
            connected_provider_ids=(),
        ),
    )
    assert snapshot is not None
    encoded = repr(snapshot)
    assert "AWS_SECRET" not in encoded
    assert "hunter2" not in encoded
    assert "password" not in encoded
