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
    advice_delivery_identity,
    build_observation_advice_snapshot,
    hook_advice_context,
    minimized_semantic_evidence_packet,
    select_advice_item,
    select_standing_item,
    should_reissue_advice,
)
from yoetz.application.observation_coordinator import (
    _materialized_advice_items,  # pyright: ignore[reportPrivateUsage]  # noqa: PLC2701
)
from yoetz.cli.observe_hooks import handle_observe
from yoetz.domain.observation import (
    AdviceSnapshot,
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
    # #241: the delivery identity is over what the agent actually receives, so
    # it must NOT move with the envelope stream the basis digest tracks.
    assert advice_delivery_identity(first) == advice_delivery_identity(latest)


def test_callable_composition_is_resolved_freshly_on_every_build() -> None:
    """The provider fact is recomputed per build, tracking live machine state (#265).

    A READY-frozen fact kept advising connect_provider after the same mapped
    session dispatched successfully. A per-build source lets current structural
    usability retire the advice and credential revocation resurface it.
    """

    facts = [
        ObservationCompositionFact(
            semantic_configured=True,
            semantic_ready=False,
            provider_factory_ids=("fireworks",),
            connected_provider_ids=(),
        ),
        ObservationCompositionFact(
            semantic_configured=True,
            semantic_ready=True,
            provider_factory_ids=("fireworks",),
            connected_provider_ids=("fireworks",),
        ),
        ObservationCompositionFact(
            semantic_configured=True,
            semantic_ready=False,
            provider_factory_ids=("fireworks",),
            connected_provider_ids=(),
        ),
    ]
    builds: list[ObservationCompositionFact] = []

    async def composition() -> ObservationCompositionFact:
        fact = facts[len(builds)]
        builds.append(fact)
        return fact

    class _Store:
        def list_envelopes(self, workspace: str) -> tuple[ObservationEnvelope, ...]:
            del workspace
            return (_envelope("hook:one", {"tool_name": "shell"}),)

        async def status(self, query: ObservationStatusQuery) -> ObservationStatus:
            del query
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
            del workspace
            return None

    builder = ObservationAdviceContextBuilder(composition=composition)

    def rule_codes(snapshot: object) -> tuple[str, ...]:
        if snapshot is None:
            return ()
        assert isinstance(snapshot, AdviceSnapshot)
        return tuple(item.rule_code for item in snapshot.ranked_items)

    missing = asyncio.run(builder.build(_COMMITMENT, _Store()))  # type: ignore[arg-type]
    connected = asyncio.run(builder.build(_COMMITMENT, _Store()))  # type: ignore[arg-type]
    revoked = asyncio.run(builder.build(_COMMITMENT, _Store()))  # type: ignore[arg-type]

    assert len(builds) == 3, "the composition source was not consulted on every build"
    assert "provider_not_ready" in rule_codes(missing)
    assert "provider_not_ready" not in rule_codes(connected)
    assert "provider_not_ready" in rule_codes(revoked)


def test_delivery_identity_is_stable_while_the_envelope_stream_grows() -> None:
    """The hook-channel identity keys on rendered content, not on evidence breadth."""

    composition = ObservationCompositionFact(
        semantic_configured=True,
        semantic_ready=False,
        provider_factory_ids=("fireworks",),
        connected_provider_ids=(),
    )

    def _build(count: int):
        built = build_observation_advice_snapshot(
            ObservationAdviceBuildInput(
                envelopes=tuple(
                    _envelope(f"hook:{index}", {"tool_name": "shell"}, pos=index)
                    for index in range(1, count + 1)
                ),
                lifecycle=ObservationLifecycle.ACTIVE,
                gaps=(),
                composition=composition,
                has_real_observation=True,
            )
        )
        assert built is not None
        return built

    one = _build(1)
    twenty = _build(20)
    assert advice_delivery_identity(one) == advice_delivery_identity(twenty)
    assert one.evidence_basis_digest != twenty.evidence_basis_digest
    assert hook_advice_context(one) == hook_advice_context(twenty)


def _gap_snapshot(count: int):
    """Gap advice over ``count`` envelopes; its evidence refs are the last three."""

    built = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=tuple(
                _envelope(f"hook:gap{index}", {"tool_name": "Read"}, pos=index)
                for index in range(1, count + 1)
            ),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=("source_lag",),
            has_real_observation=True,
        )
    )
    assert built is not None
    assert built.ranked_items[0].rule_code == "observation_gap_or_stale"
    return built


def test_delivery_identity_ignores_evidence_refs_that_are_a_rolling_window() -> None:
    """A rule may cite the last three envelopes; that citation is not a new condition.

    ``observation_gap_or_stale`` cites ``envelopes[-3:]``, so an identity that
    folded in the evidence ref moved on every single hook and stormed exactly
    like the standing condition #241 fixed (measured: delivered on 12 of 12
    consecutive PostToolUse hooks).
    """

    first = _gap_snapshot(3)
    later = _gap_snapshot(9)

    assert first.ranked_items[0].evidence_refs != later.ranked_items[0].evidence_refs
    assert hook_advice_context(first) != hook_advice_context(later), (
        "the delivered text must still carry its (moved) evidence reference"
    )
    assert advice_delivery_identity(first) == advice_delivery_identity(later), (
        "the dedup key moved with a rolling evidence window; the advice will storm"
    )


def test_finding_identity_ignores_evidence_refs_that_are_a_rolling_window() -> None:
    """A standing condition remains one durable finding as its citations move (#216)."""

    first = _gap_snapshot(3)
    later = _gap_snapshot(9)

    assert first.ranked_items[0].evidence_refs != later.ranked_items[0].evidence_refs
    assert first.evidence_basis_digest != later.evidence_basis_digest
    assert first.ranked_items[0].finding_id == later.ranked_items[0].finding_id


def test_delivery_identity_distinguishes_successive_failed_commands() -> None:
    shared_prefix = "same-correlation-prefix-" + "x" * 40
    first = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=(
                _envelope(
                    "hook:fail-a",
                    {
                        "tool_name": "shell",
                        "exit_status": 2,
                        "correlation_id": shared_prefix + "-a",
                    },
                ),
            ),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            has_real_observation=True,
        )
    )
    later = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=(
                _envelope(
                    "hook:fail-a",
                    {
                        "tool_name": "shell",
                        "exit_status": 2,
                        "correlation_id": shared_prefix + "-a",
                    },
                    pos=1,
                ),
                _envelope(
                    "hook:resolve-a",
                    {
                        "tool_name": "shell",
                        "exit_status": 0,
                        "correlation_id": shared_prefix + "-a",
                    },
                    pos=2,
                ),
                _envelope(
                    "hook:fail-b",
                    {
                        "tool_name": "shell",
                        "exit_status": 3,
                        "correlation_id": shared_prefix + "-b",
                    },
                    pos=3,
                ),
            ),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            has_real_observation=True,
        )
    )
    assert first is not None and later is not None
    assert first.ranked_items[0].summary == later.ranked_items[0].summary
    assert first.ranked_items[0].finding_id != later.ranked_items[0].finding_id
    assert advice_delivery_identity(first) != advice_delivery_identity(later)


def test_delivery_identity_changes_with_rule_code_or_next_action() -> None:
    """Everything that names the condition stays in the key."""

    import dataclasses

    snapshot = _gap_snapshot(3)
    item = snapshot.ranked_items[0]
    baseline = advice_delivery_identity(snapshot, item=item)

    other_rule = dataclasses.replace(item, rule_code="provider_not_ready")
    other_action = dataclasses.replace(item, recommended_next_action="connect_provider")
    other_summary = dataclasses.replace(item, summary="A different summary")
    other_detail = dataclasses.replace(item, detail="A different detail")
    for changed in (other_rule, other_action, other_summary, other_detail):
        assert advice_delivery_identity(snapshot, item=changed) != baseline


def test_delivery_identity_changes_when_the_rendered_text_changes() -> None:
    standing = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=(_envelope("hook:one", {"tool_name": "shell"}),),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=ObservationCompositionFact(
                semantic_configured=True,
                semantic_ready=False,
                provider_factory_ids=("fireworks",),
                connected_provider_ids=(),
            ),
            has_real_observation=True,
        )
    )
    failed = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=(
                _envelope(
                    "hook:one",
                    {"tool_name": "shell", "exit_status": 1, "correlation_id": "x1"},
                ),
            ),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            has_real_observation=True,
        )
    )
    assert standing is not None and failed is not None
    assert hook_advice_context(standing) != hook_advice_context(failed)
    assert advice_delivery_identity(standing) != advice_delivery_identity(failed)


def test_standing_items_fall_through_to_the_first_actionable_item() -> None:
    """Withholding a standing item must never suppress advice ranked below it."""

    snapshot = build_observation_advice_snapshot(
        ObservationAdviceBuildInput(
            envelopes=(_envelope("hook:one", {"tool_name": "shell", "claim_kind": "semantic"}),),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=ObservationCompositionFact(
                semantic_configured=True,
                semantic_ready=False,
                provider_factory_ids=("fireworks",),
                connected_provider_ids=(),
            ),
            has_real_observation=True,
        )
    )
    assert snapshot is not None
    assert [item.rule_code for item in snapshot.ranked_items] == [
        "provider_not_ready",
        "semantic_claim_without_attempt",
    ]
    permitted = select_advice_item(snapshot, allow_standing=True)
    withheld = select_advice_item(snapshot, allow_standing=False)
    standing = select_standing_item(snapshot)
    assert permitted is not None and permitted.rule_code == "provider_not_ready"
    assert withheld is not None and withheld.rule_code == "semantic_claim_without_attempt"
    assert standing is not None and standing.rule_code == "provider_not_ready"


def test_suppression_identity_still_tracks_evidence_for_materialization() -> None:
    """observation_advice_history keys row distinctness on evidence; do not weaken it.

    ``suppression_identity`` also carries the coordinator's materialization
    retry-safety and ``observe status``'s advice_frontier, so the #241 fix adds
    a second identity rather than redefining this one.
    """

    composition = ObservationCompositionFact(
        semantic_configured=True,
        semantic_ready=False,
        provider_factory_ids=("fireworks",),
        connected_provider_ids=(),
    )

    def _build(count: int):
        built = build_observation_advice_snapshot(
            ObservationAdviceBuildInput(
                envelopes=tuple(
                    _envelope(f"hook:{index}", {"tool_name": "shell"}, pos=index)
                    for index in range(1, count + 1)
                ),
                lifecycle=ObservationLifecycle.ACTIVE,
                gaps=(),
                composition=composition,
                has_real_observation=True,
            )
        )
        assert built is not None
        return built

    narrow = _build(1)
    broad = _build(5)
    assert narrow.ranked_finding_ids == broad.ranked_finding_ids
    assert narrow.suppression_identity != broad.suppression_identity


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
    assert (
        store.peek_advice_for_delivery(
            workspace, session_commitment=store.session_commitment("advice-1")
        )
        is None
    )
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
