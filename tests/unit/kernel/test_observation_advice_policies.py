"""Unit tests for deterministic observation-advice policies."""

from __future__ import annotations

from yoetz.domain.findings import FindingKind
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationLifecycle,
    ObservationSource,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.kernel.policies.observation_advice import (
    ObservationAdviceContext,
    ObservationCheckFact,
    ObservationCompositionFact,
    ObservationInspectFact,
    observation_advice_findings,
)

_COMMITMENT = "hmac-sha256:" + "a" * 64
_DIGEST = "sha256:" + "b" * 64
_TIME = Timestamp("2026-07-22T21:00:00.000Z")


def _cursor(event_pos: int = 1) -> ObservationCursor:
    return ObservationCursor(
        source_generation=1,
        byte_position=event_pos * 10,
        event_position=event_pos,
        last_source_commitment=_COMMITMENT,
        mapping_version="codex-obs-hook/1.0.0",
    )


def _envelope(
    event_kind: str,
    *,
    pos: int,
    identity: str,
    payload: dict[str, object],
    gaps: tuple[str, ...] = (),
) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=_COMMITMENT,
        event_kind=event_kind,
        source_identity=identity,
        source=ObservationSource.CODEX_HOOK,
        cursor=_cursor(pos),
        receipt_time=_TIME,
        structural_payload=JsonObject(payload),
        content_object_refs=(),
        gap_codes=gaps,
    )


def _rules(context: ObservationAdviceContext) -> set[str]:
    return {item.rule_code for item in observation_advice_findings(context)}


def test_failed_command_left_unresolved() -> None:
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=1,
            identity="hook:fail1",
            payload={"tool_name": "shell", "exit_status": 1, "correlation_id": "c1"},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
        )
    )
    assert "failed_command_unresolved" in rules


def test_failed_command_cleared_by_retry() -> None:
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=1,
            identity="hook:fail1",
            payload={"tool_name": "shell", "exit_status": 1, "correlation_id": "c1"},
        ),
        _envelope(
            "PostToolUse",
            pos=2,
            identity="hook:ok1",
            payload={"tool_name": "shell", "exit_status": 0, "correlation_id": "c1"},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
        )
    )
    assert "failed_command_unresolved" not in rules


def test_edit_after_successful_check() -> None:
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=1,
            identity="hook:check",
            payload={"tool_name": "shell", "exit_status": 0},
        ),
        _envelope(
            "PostToolUse",
            pos=2,
            identity="hook:edit",
            payload={
                "tool_name": "apply_patch",
                "action": "write",
                "changed_paths_digest": _DIGEST,
            },
        ),
    )
    findings = observation_advice_findings(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
        )
    )
    assert any(item.rule_code == "edit_after_successful_check" for item in findings)
    assert any(item.kind is FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE for item in findings)


def test_completion_without_verification() -> None:
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=1,
            identity="hook:claim",
            payload={"tool_name": "publish_work", "claim_kind": "completion"},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            check_facts=(),
        )
    )
    assert "completion_without_verification" in rules


def test_static_test_for_live_claim() -> None:
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=1,
            identity="hook:liveclaim",
            payload={"tool_name": "publish_work", "claim_kind": "live_wire_ok"},
        ),
        _envelope(
            "PostToolUse",
            pos=2,
            identity="hook:pytest",
            payload={"tool_name": "pytest", "exit_status": 0, "mapping_hint": "static"},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
        )
    )
    assert "static_test_for_live_claim" in rules


def test_subagent_finding_unaddressed() -> None:
    envelopes = (
        _envelope(
            "SubagentStop",
            pos=1,
            identity="hook:sub",
            payload={"subagent_id": "sub-1", "result_status": "finding", "success": False},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
        )
    )
    assert "subagent_finding_unaddressed" in rules


def test_change_outside_plan() -> None:
    plan = ("sha256:" + "c" * 64,)
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=1,
            identity="hook:chg",
            payload={"tool_name": "apply_patch", "changed_paths_digest": _DIGEST},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            plan_path_digests=plan,
            inspect_fact=ObservationInspectFact(
                selection_digest=_DIGEST,
                relative_paths=("src/a.py",),
                changed_paths_digest=_DIGEST,
            ),
        )
    )
    assert "change_outside_plan" in rules


def test_observation_gap_or_stale() -> None:
    rules = _rules(
        ObservationAdviceContext(
            envelopes=(),
            lifecycle=ObservationLifecycle.DEGRADED,
            gaps=(ObservationGapCode.SOURCE_LAG.value,),
        )
    )
    assert "observation_gap_or_stale" in rules


def test_provider_not_ready() -> None:
    rules = _rules(
        ObservationAdviceContext(
            envelopes=(),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=ObservationCompositionFact(
                semantic_configured=True,
                semantic_ready=False,
                provider_factory_ids=("openai",),
                connected_provider_ids=(),
            ),
        )
    )
    assert "provider_not_ready" in rules


def test_registry_lag_alone_does_not_emit_provider_not_ready() -> None:
    """A structurally usable provider absent from the lazy registry is not "not ready" (#265).

    Registry activation is repository-scoped and re-established automatically at
    dispatch, so a configured provider missing from the connected set proves
    nothing the operator can act on with connect_provider.
    """

    rules = _rules(
        ObservationAdviceContext(
            envelopes=(),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=ObservationCompositionFact(
                semantic_configured=True,
                semantic_ready=True,
                provider_factory_ids=("fireworks",),
                connected_provider_ids=(),
            ),
        )
    )
    assert "provider_not_ready" not in rules


def test_provider_not_ready_requires_semantic_to_be_configured() -> None:
    """With semantic disabled, connect_provider advice has no action to recommend (#265)."""

    rules = _rules(
        ObservationAdviceContext(
            envelopes=(),
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            composition=ObservationCompositionFact(
                semantic_configured=False,
                semantic_ready=False,
                provider_factory_ids=("fireworks",),
                connected_provider_ids=(),
            ),
        )
    )
    assert "provider_not_ready" not in rules


def test_provider_not_ready_names_the_unusable_configured_provider() -> None:
    """The structural condition keeps naming the configured provider as evidence."""

    context = ObservationAdviceContext(
        envelopes=(),
        lifecycle=ObservationLifecycle.ACTIVE,
        gaps=(),
        composition=ObservationCompositionFact(
            semantic_configured=True,
            semantic_ready=False,
            provider_factory_ids=("fireworks",),
            connected_provider_ids=(),
        ),
    )
    candidates = observation_advice_findings(context)
    item = next(item for item in candidates if item.rule_code == "provider_not_ready")
    assert item.evidence_refs == ("fireworks",)
    assert item.next_action == "connect_provider"


def test_semantic_claim_without_attempt() -> None:
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=1,
            identity="hook:sem",
            payload={"tool_name": "publish_work", "claim_kind": "semantic_review_pass"},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
        )
    )
    assert "semantic_claim_without_attempt" in rules


def test_check_fact_binds_edit_staleness() -> None:
    envelopes = (
        _envelope(
            "PostToolUse",
            pos=5,
            identity="hook:edit",
            payload={"tool_name": "apply_patch", "action": "write"},
        ),
    )
    rules = _rules(
        ObservationAdviceContext(
            envelopes=envelopes,
            lifecycle=ObservationLifecycle.ACTIVE,
            gaps=(),
            check_facts=(
                ObservationCheckFact(
                    approval_commitment=_DIGEST,
                    subject_state_digest=_DIGEST,
                    status="passed",
                    cursor_event_position=2,
                ),
            ),
        )
    )
    assert "edit_after_successful_check" in rules
