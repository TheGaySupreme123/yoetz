"""Findings as a converging to-do list: terminal states and review rounds (issue #905)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from builders.policy_cases import evt, finding_record, fnd, obl, record
from yoetz.domain.events import (
    CheckMode,
    CheckRecordedPayload,
    EventDraft,
    EventSchema,
    PolicyVersion,
    ResponseRecordedPayload,
)
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    CheckVerdict,
    Finding,
    FindingKind,
    FindingOrigin,
    PriorFindingVerdictRecord,
    ResponseDisposition,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.domain.values import FindingId, Frontier, timestamp_from_string
from yoetz.kernel.finding_resolution import (
    apply_check_resolution,
    apply_check_rulings,
    finding_is_resolved,
    reopen_findings_resolved_by,
)
from yoetz.kernel.finding_todo import (
    TERMINAL_TODO_STATES,
    TODO_TRANSITIONS,
    FindingTodoState,
    finding_blocks_receipt,
    finding_todo,
    finding_todo_state,
    todo_counts,
)
from yoetz.kernel.projections import (
    FindingProjectionRecord,
    ProjectionRecord,
    ProjectionState,
    empty_projection_state,
    projection_from_snapshot,
    projection_snapshot,
)
from yoetz.ports.semantic import SamplingParams
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
)
from yoetz.protocol.errors import ProtocolValueError
from yoetz.protocol.models import (
    CheckPolicyExecutionModel,
    CheckScopeModel,
    SemanticReason,
    SemanticStatus,
)

_DIGEST = "sha256:" + "3" * 64


def _coverage() -> Coverage:
    return Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.SERVICE_AUTHENTICATED,
        artifact_observation=ArtifactObservation.PUBLISHED_ONLY,
        evidence_immutability=EvidenceImmutability.METADATA_ONLY,
        ledger_freshness=LedgerFreshness.CURRENT,
        check_types=(CheckType.DETERMINISTIC,),
        known_gaps=(),
    )


def _finding(number: int, origin: FindingOrigin = FindingOrigin.DETERMINISTIC) -> Finding:
    kind = FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS
    return Finding(
        finding_id=fnd(number),
        kind=kind,
        origin=origin,
        priority=FINDING_KIND_TRAITS[kind][0],
        summary="A completion claim covers an open obligation.",
        detail="Resolve or revise the obligation before claiming completion.",
        subject_refs=(obl(number),),
        policy_id="work-integrity",
        policy_version="0.1.0",
        subject_frontier=Frontier(3, _DIGEST),
        coverage=_coverage(),
        provenance=None,
    )


def _semantic(number: int) -> Finding:
    provenance = SemanticProvenance(
        provider="fake",
        endpoint_profile_id="fake",
        endpoint_profile_version="1.0.0",
        model="fake/model",
        sdk_version="1.0.0",
        prompt_digest=_DIGEST,
        schema_digest=_DIGEST,
        policy_digest=_DIGEST,
        privacy_policy_digest=_DIGEST,
        sampling_params=SamplingParams(128),
        latency_ms=1,
        semantic_attempt_id="att_00000000-0000-4000-8000-000000000001",
        dispatch_kind=SemanticDispatchKind.EXTERNAL,
        privacy_receipt_id="egr_00000000-0000-4000-8000-000000000001",
        status=SemanticStatus.SUCCEEDED,
        reason=SemanticReason.SEMANTIC_COMPLETED,
        provider_request_id="fake-1",
        egress_authorization_id="aut_00000000-0000-4000-8000-000000000001",
        request_commitment="hmac-sha256:" + "b" * 64,
    )
    coverage = replace(
        _coverage(), check_types=(CheckType.DETERMINISTIC, CheckType.SEMANTIC_MODEL_DERIVED)
    )
    base = _finding(number)
    return Finding(
        finding_id=base.finding_id,
        kind=base.kind,
        origin=FindingOrigin.SEMANTIC_MODEL_DERIVED,
        priority=base.priority,
        summary=base.summary,
        detail=base.detail,
        subject_refs=base.subject_refs,
        policy_id=base.policy_id,
        policy_version=base.policy_version,
        subject_frontier=base.subject_frontier,
        coverage=coverage,
        provenance=provenance,
    )


def _check(
    *,
    tested: int = 8,
    returned: tuple[object, ...] = (),
    rulings: tuple[tuple[int, str], ...] = (),
) -> CheckRecordedPayload:
    # ``model_construct`` style: the fold reads only these fields, so skip semantic validation.
    payload = object.__new__(CheckRecordedPayload)
    for name, value in (
        ("mode", CheckMode.DETERMINISTIC_ONLY),
        ("policies", (PolicyVersion("work-integrity", "0.1.0"),)),
        ("scope", CheckScopeModel(claim_ids=(), obligation_ids=())),
        (
            "policy_executions",
            (
                CheckPolicyExecutionModel(
                    policy_id="work-integrity",
                    policy_version="0.1.0",
                    outcome="run",
                    reason="completed",
                ),
            ),
        ),
        ("subject_frontier", Frontier(tested, _DIGEST)),
        ("verdict", CheckVerdict.ACTION_REQUIRED if returned else CheckVerdict.NO_ISSUE_DETECTED),
        ("returned_finding_ids", returned),
        ("suppressed_count", 1),  # suppression keeps ``apply_check_resolution`` from proving
        ("coverage", _coverage()),
        ("semantic_status", SemanticStatus.NOT_REQUESTED),
        ("semantic_reason", SemanticReason.DETERMINISTIC_MODE),
        ("engine_version", "0.1.0"),
        ("projection_version", "yoetz/0.1.0"),
        ("semantic_provenance", None),
        ("semantic_conclusion", None),
        (
            "prior_finding_verdicts",
            tuple(
                PriorFindingVerdictRecord(fnd(number), verdict, ()) for number, verdict in rulings
            ),
        ),
    ):
        object.__setattr__(payload, name, value)
    return payload


def _response(
    number: int, disposition: ResponseDisposition
) -> ProjectionRecord[ResponseRecordedPayload]:
    reason = None if disposition is ResponseDisposition.ACKNOWLEDGED else "A recorded reason."
    return record(
        ResponseRecordedPayload(fnd(number), Frontier(5, _DIGEST), disposition, reason), 6
    )


def _state(
    findings: Mapping[FindingId, FindingProjectionRecord],
    responses: Mapping[FindingId, ProjectionRecord[ResponseRecordedPayload]] | None = None,
) -> ProjectionState:
    return replace(
        empty_projection_state(),
        frontier=20,
        head_digest=_DIGEST,
        findings=findings,
        responses={} if responses is None else responses,
        freshness=LedgerFreshness.CURRENT,
    )


def test_the_transition_table_is_data_and_terminal_states_have_no_exit() -> None:
    assert {source for source, _cause, _target in TODO_TRANSITIONS} == {FindingTodoState.OPEN}
    assert {target for _source, _cause, target in TODO_TRANSITIONS} == set(TERMINAL_TODO_STATES)
    assert FindingTodoState.OPEN not in TERMINAL_TODO_STATES


def test_a_local_finding_returned_again_over_later_state_counts_one_round() -> None:
    findings = {fnd(1): finding_record(_finding(1), 4)}
    raising = _check(tested=3, returned=(fnd(1),))  # the check that raised it: same subject
    apply_check_rulings(findings, {}, raising, evt(9))
    assert findings[fnd(1)].review_rounds == 0
    for event in (10, 11):
        apply_check_rulings(findings, {}, _check(returned=(fnd(1),)), evt(event))
    assert findings[fnd(1)].review_rounds == 2
    todo = finding_todo(_state(dict(findings)), fnd(1), attempt_budget=2)
    assert todo.state is FindingTodoState.OPEN and todo.budget_reached


@pytest.mark.parametrize("verdict", ["still_present", "answered_not_fixed", "unassessable"])
def test_an_open_ruling_counts_a_round_and_never_closes(verdict: str) -> None:
    findings = {fnd(1): finding_record(_semantic(1), 4)}
    apply_check_rulings(findings, {}, _check(rulings=((1, verdict),)), evt(9))
    assert findings[fnd(1)].review_rounds == 1
    assert finding_todo_state(_state(dict(findings)), fnd(1)) is FindingTodoState.OPEN


def test_withdrawn_latches_only_after_a_readable_rejection() -> None:
    findings = {
        fnd(1): finding_record(_semantic(1), 4),
        fnd(2): finding_record(_semantic(2), 4),
    }
    responses = {
        fnd(1): _response(1, ResponseDisposition.REJECTED),
        fnd(2): _response(2, ResponseDisposition.ACKNOWLEDGED),
    }
    apply_check_rulings(
        findings, responses, _check(rulings=((1, "withdrawn"), (2, "withdrawn"))), evt(9)
    )
    assert findings[fnd(1)].rejection_accepted_by_check_event_id == evt(9)
    assert findings[fnd(2)].rejection_accepted_by_check_event_id is None
    state = _state(dict(findings), dict(responses))
    assert finding_todo_state(state, fnd(1)) is FindingTodoState.REJECTION_ACCEPTED
    assert finding_blocks_receipt(state, fnd(1)) is False
    assert finding_blocks_receipt(state, fnd(2)) is True
    assert todo_counts(state, (fnd(1), fnd(2))).rejection_accepted == 1


def test_rejection_accepted_never_upgrades_and_redaction_drops_the_latch() -> None:
    latched = replace(finding_record(_finding(1), 4), rejection_accepted_by_check_event_id=evt(9))
    findings = {fnd(1): latched}
    clean = _check()
    object.__setattr__(clean, "suppressed_count", 0)
    control = {fnd(1): finding_record(_finding(1), 4)}
    apply_check_resolution(control, clean, evt(12))
    assert control[fnd(1)].resolved_by_check_event_id == evt(12), "the check does qualify"
    apply_check_resolution(findings, clean, evt(12))
    assert findings[fnd(1)].resolved_by_check_event_id is None, "no upgrade out of terminal"
    reopen_findings_resolved_by(findings, frozenset({evt(9)}))
    assert findings[fnd(1)].rejection_accepted_by_check_event_id is None


def test_acknowledged_not_done_is_never_resolved_and_always_blocks() -> None:
    proven = finding_record(_finding(1), 4, resolved_by_check_event_id=evt(9))
    state = _state(
        {fnd(1): proven}, {fnd(1): _response(1, ResponseDisposition.ACKNOWLEDGED_NOT_DONE)}
    )
    assert finding_is_resolved(state, fnd(1)) is False
    assert finding_todo_state(state, fnd(1)) is FindingTodoState.ACKNOWLEDGED_NOT_DONE
    assert finding_blocks_receipt(state, fnd(1)) is True


def test_todo_facts_round_trip_and_stay_absent_until_set() -> None:
    plain = finding_record(_finding(1), 4)
    counted = replace(
        finding_record(_semantic(2), 5),
        review_rounds=3,
        rejection_accepted_by_check_event_id=evt(9),
    )
    state = _state({fnd(1): plain, fnd(2): counted})
    snapshot = projection_snapshot(state)
    rows = snapshot["findings"]
    assert isinstance(rows, dict)
    assert "review_rounds" not in rows[fnd(1)]  # type: ignore[operator]
    assert "rejection_accepted_by_check_event_id" not in rows[fnd(1)]  # type: ignore[operator]
    decoded = projection_from_snapshot(snapshot)
    assert decoded == state
    assert canonical_encode(projection_snapshot(decoded)) == canonical_encode(snapshot)


def test_budget_is_bounded() -> None:
    state = _state({fnd(1): finding_record(_finding(1), 4)})
    for bad in (0, 51, True):
        with pytest.raises(ValueError, match="finding_attempt_budget_invalid"):
            finding_todo(state, fnd(1), attempt_budget=bad)  # type: ignore[arg-type]


def test_acknowledged_not_done_needs_a_reason_and_rides_only_response_recorded_1_1_0() -> None:
    with pytest.raises(ProtocolValueError):
        ResponseRecordedPayload(
            fnd(1), Frontier(5, _DIGEST), ResponseDisposition.ACKNOWLEDGED_NOT_DONE
        )
    payload = ResponseRecordedPayload(
        fnd(1), Frontier(5, _DIGEST), ResponseDisposition.ACKNOWLEDGED_NOT_DONE, "Out of scope."
    )
    at = timestamp_from_string("2026-09-30T12:00:00.000Z")
    EventDraft(evt(1), EventSchema("response_recorded", "1.1.0"), at, (evt(2),), payload, (), ())
    with pytest.raises(ProtocolValueError, match="invalid_event_schema"):
        EventDraft(
            evt(1), EventSchema("response_recorded", "1.0.0"), at, (evt(2),), payload, (), ()
        )
    rejected = ResponseRecordedPayload(
        fnd(1), Frontier(5, _DIGEST), ResponseDisposition.REJECTED, "Not applicable."
    )
    with pytest.raises(ProtocolValueError, match="invalid_event_schema"):
        EventDraft(
            evt(1), EventSchema("response_recorded", "1.1.0"), at, (evt(2),), rejected, (), ()
        )
