"""Closure readiness as a checklist: the kernel split and its per-request facts (issue #913)."""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from builders.replay import replay_records
from yoetz.domain.events import (
    AcceptedEvent,
    CheckMode,
    CheckRecordedPayload,
    LedgerRecord,
    encode_payload,
)
from yoetz.domain.findings import SemanticDispatchKind, SemanticProvenance
from yoetz.domain.values import finding_id, obligation_id
from yoetz.kernel.closure_readiness import (
    ClosureReadinessFacts,
    closure_readiness_facts,
    derive_closure_readiness,
)
from yoetz.kernel.receipt_capacity import receipt_blocking_finding_count
from yoetz.kernel.reducers import invalidates_recorded_check, replay
from yoetz.ports.semantic import SamplingParams
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.coverage import CheckType
from yoetz.protocol.models import SemanticReason, SemanticStatus

_FINDING = finding_id("fnd_91300000-0000-4000-8000-000000000001")
_OTHER_FINDING = finding_id("fnd_91300000-0000-4000-8000-000000000002")
_OBLIGATION = obligation_id("obl_91300000-0000-4000-8000-000000000003")
_DIGEST = "sha256:" + "5" * 64
# The standing Codex host gaps plus the deliberate local-only choice (DeepSWE v2 bandit B).
_BANDIT_B_GAPS = (
    "content_unselected",
    "host_outcome_unavailable",
    "semantic_review_not_requested",
    "unpaired_event",
)


def _facts(**changes: object) -> ClosureReadinessFacts:
    base = ClosureReadinessFacts(
        check_applicability="applicable",
        semantic_review_current=False,
        receipt_blocking_finding_ids=(),
        acknowledged_finding_ids=(),
        acknowledged_obligation_ids=(),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_only_standing_limitations_read_ready_with_limitations() -> None:
    split = derive_closure_readiness(
        ("coverage_gaps_declared",), _BANDIT_B_GAPS, _facts(), semantic_review_required=False
    )
    assert split.state == "ready_with_limitations"
    assert split.agent_actionable == ()
    assert split.standing_limitations == _BANDIT_B_GAPS
    assert split.acknowledged_not_done == ()


def test_no_gaps_and_nothing_left_reads_ready() -> None:
    split = derive_closure_readiness((), (), _facts(), semantic_review_required=False)
    assert split.state == "ready"
    assert split.agent_actionable == split.standing_limitations == ()


@pytest.mark.parametrize(
    "condition",
    (
        "obligations_open",
        "findings_unanswered",
        "receipt_findings_unresolved",
        "no_plan_published",
        "no_obligations_declared",
        "projection_stale",
    ),
)
def test_open_work_stays_agent_actionable_beside_standing_gaps(condition: str) -> None:
    split = derive_closure_readiness(
        (condition, "coverage_gaps_declared"),
        _BANDIT_B_GAPS,
        _facts(receipt_blocking_finding_ids=(_FINDING,)),
        semantic_review_required=False,
    )
    assert split.state == "action_required"
    assert split.agent_actionable == (condition,)
    assert split.standing_limitations == _BANDIT_B_GAPS


def test_actionable_gap_codes_stay_agent_actionable() -> None:
    split = derive_closure_readiness(
        ("coverage_gaps_declared",),
        (*_BANDIT_B_GAPS, "completion_plan_not_claimed", "check_coverage:missing_ref"),
        _facts(),
        semantic_review_required=False,
    )
    assert split.state == "action_required"
    assert split.agent_actionable == ("completion_plan_not_claimed", "missing_ref")


@pytest.mark.parametrize("applicability", ("not_recorded", "not_applicable"))
def test_a_missing_or_superseded_check_is_the_one_recheck_readiness_asks_for(
    applicability: str,
) -> None:
    split = derive_closure_readiness(
        ("coverage_gaps_declared",),
        _BANDIT_B_GAPS,
        _facts(check_applicability=applicability),
        semantic_review_required=False,
    )
    assert split.state == "action_required"
    assert split.agent_actionable == ("check_" + applicability,)


def test_an_unreadable_check_payload_is_disclosed_not_rechecked() -> None:
    split = derive_closure_readiness(
        (), (), _facts(check_applicability="payload_unavailable"), semantic_review_required=False
    )
    assert split.state == "ready_with_limitations"
    assert split.standing_limitations == ("check_payload_unavailable",)


@pytest.mark.parametrize(
    ("required", "current", "state"),
    (
        (False, False, "ready_with_limitations"),
        (True, True, "ready_with_limitations"),
        (True, False, "action_required"),
    ),
)
def test_semantic_review_not_requested_follows_the_route(
    required: bool, current: bool, state: str
) -> None:
    split = derive_closure_readiness(
        ("coverage_gaps_declared",),
        _BANDIT_B_GAPS,
        _facts(semantic_review_current=current),
        semantic_review_required=required,
    )
    assert split.state == state
    assert ("semantic_review_not_requested" in split.agent_actionable) is (
        state == "action_required"
    )


def test_only_an_acknowledgement_moves_a_receipt_blocking_finding_off_the_agent_list() -> None:
    blocking = ("receipt_findings_unresolved", "coverage_gaps_declared")
    partly = derive_closure_readiness(
        blocking,
        _BANDIT_B_GAPS,
        _facts(
            receipt_blocking_finding_ids=(_FINDING, _OTHER_FINDING),
            acknowledged_finding_ids=(_FINDING,),
        ),
        semantic_review_required=False,
    )
    assert partly.state == "action_required"
    assert partly.agent_actionable == ("receipt_findings_unresolved",)
    assert partly.acknowledged_not_done == (_FINDING,)

    fully = derive_closure_readiness(
        blocking,
        _BANDIT_B_GAPS,
        _facts(
            receipt_blocking_finding_ids=(_FINDING,),
            acknowledged_finding_ids=(_FINDING,),
            acknowledged_obligation_ids=(_OBLIGATION,),
        ),
        semantic_review_required=False,
    )
    assert fully.state == "ready_with_limitations"
    assert fully.agent_actionable == ()
    assert fully.acknowledged_not_done == (_FINDING, _OBLIGATION)
    assert fully.acknowledged_not_done_count == 2


def test_missing_facts_never_assume_an_acknowledgement() -> None:
    split = derive_closure_readiness(
        ("receipt_findings_unresolved", "coverage_gaps_declared"),
        _BANDIT_B_GAPS,
        None,
        semantic_review_required=False,
    )
    assert split.agent_actionable == ("receipt_findings_unresolved",)
    assert split.acknowledged_not_done == ()


def test_unknown_code_is_named_and_keeps_the_task_actionable() -> None:
    split = derive_closure_readiness(
        ("coverage_gaps_declared",),
        (*_BANDIT_B_GAPS, "gap_from_a_newer_build"),
        _facts(),
        semantic_review_required=False,
    )
    assert split.state == "action_required"
    assert split.agent_actionable == ("unclassified_gap:gap_from_a_newer_build",)


def _provenance() -> SemanticProvenance:
    return SemanticProvenance(
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


def _prefix_through_check() -> tuple[LedgerRecord, ...]:
    records = replay_records("all-event-families")
    index = next(
        position
        for position, record in enumerate(records)
        if type(record) is AcceptedEvent and type(record.payload) is CheckRecordedPayload
    )
    return tuple(records[: index + 1])


def test_facts_follow_the_receipt_applicability_rule_on_a_real_replay() -> None:
    prefix = _prefix_through_check()
    facts = closure_readiness_facts(replay(prefix), prefix)
    assert facts.check_applicability == "applicable"

    genesis = tuple(record for record in prefix if record.schema.name == "session_opened")
    assert closure_readiness_facts(replay(genesis), genesis).check_applicability == "not_recorded"

    records = tuple(replay_records("all-event-families"))
    projection = replay(records)
    full = closure_readiness_facts(projection, records)
    # The receipt-blocking set is exactly the one the compact row counts.
    assert len(full.receipt_blocking_finding_ids) == receipt_blocking_finding_count(projection)
    # Nothing in a recorded ledger is acknowledged unless a response says so.
    assert full.acknowledged_finding_ids == ()


def test_semantic_currency_needs_a_completed_review_with_no_later_material_change() -> None:
    prefix = _prefix_through_check()
    check = cast(AcceptedEvent, prefix[-1])
    payload = cast(CheckRecordedPayload, check.payload)
    assert payload.semantic_status is not SemanticStatus.SUCCEEDED
    assert closure_readiness_facts(replay(prefix), prefix).semantic_review_current is False

    reviewed_payload = replace(
        payload,
        mode=CheckMode.SEMANTIC_REQUIRED,
        semantic_status=SemanticStatus.SUCCEEDED,
        semantic_reason=SemanticReason.SEMANTIC_COMPLETED,
        semantic_provenance=_provenance(),
        coverage=replace(
            payload.coverage,
            check_types=(CheckType.DETERMINISTIC, CheckType.SEMANTIC_MODEL_DERIVED),
        ),
    )
    succeeded = replace(
        check,
        payload=reviewed_payload,
        projection_locator=replace(
            check.projection_locator,
            canonical_payload_digest=canonical_digest(encode_payload(reviewed_payload)),
        ),
    )
    reviewed = (*prefix[:-1], succeeded)
    assert closure_readiness_facts(replay(prefix), reviewed).semantic_review_current is True
    records = tuple(replay_records("all-event-families"))
    later = records[len(prefix) :]
    material = next(
        position
        for position, record in enumerate(later)
        if invalidates_recorded_check(
            record, check.ledger.ingestion_sequence, payload.returned_finding_ids
        )
    )
    suffix = later[: material + 1]
    assert (
        closure_readiness_facts(replay(prefix), (*reviewed, *suffix)).semantic_review_current
        is False
    )
