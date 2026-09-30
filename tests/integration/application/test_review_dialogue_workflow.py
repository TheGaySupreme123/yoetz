"""The review dialogue survives the real ledger and reaches the next review (issue #905).

These cases run the real ready composition with a hermetic reviewer: a challenge becomes an
AI-powered finding whose challenge fields are recorded on the ledger (``finding_recorded/1.4.0``),
the agent answers it, and the next check's review case carries that finding, what the reviewer
asked, and the answer in the prior-findings section. A projection rebuilt from the recorded events
reads exactly what the live projection holds.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, cast

import pytest

from builders.projection_workflow import build_projection_application, frontier_json, request_base
from builders.start_application import protocol_id, start_request
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.semantic_case import build_semantic_case
from yoetz.application.service import Application
from yoetz.domain.events import CheckRecordedPayload, EventSchema, LedgerRecord
from yoetz.domain.findings import (
    Finding,
    FindingKind,
    FindingOrigin,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
from yoetz.kernel.finding_resolution import finding_resolution_explanation
from yoetz.kernel.projections import ProjectionState, projection_snapshot
from yoetz.kernel.reducers import replay
from yoetz.ports.ledger import CheckCommitResult, FrozenCase
from yoetz.ports.semantic import (
    PriorFindingVerdict,
    ReviewerChallenge,
    SamplingParams,
    SemanticCase,
    SemanticJudgment,
)
from yoetz.protocol.canonical import JsonValue, strict_json_parse
from yoetz.protocol.models import (
    CheckRequest,
    PublishWorkRequest,
    RespondRequest,
    SemanticReason,
    SemanticStatus,
)

pytestmark = pytest.mark.anyio

_DIGEST = "sha256:" + "7" * 64
type _Evaluator = Callable[..., Awaitable[FinalSemanticEvaluation]]


def _provenance(seed: int) -> SemanticProvenance:
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
        semantic_attempt_id=protocol_id("att_", seed),
        dispatch_kind=SemanticDispatchKind.EXTERNAL,
        privacy_receipt_id=protocol_id("egr_", seed + 1),
        status=SemanticStatus.SUCCEEDED,
        reason=SemanticReason.SEMANTIC_COMPLETED,
        provider_request_id=f"fake-dialogue-{seed}",
        egress_authorization_id=protocol_id("aut_", seed + 2),
        request_commitment="hmac-sha256:" + "b" * 64,
    )


_DISCREPANCY = "The completion claim covers an obligation whose exact-version check is open."


def _challenge(
    *refs: str, summary: str = "Required llvmlite 0.46.0 verification remains open"
) -> ReviewerChallenge:
    return ReviewerChallenge(
        FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
        summary,
        tuple(sorted(refs)),
        _DISCREPANCY,
        "The package index may be unreachable from this sandbox.",
        "Make one concrete authorized attempt to obtain llvmlite 0.46.0, then run the tests.",
        "act",
        "The recorded install failure may already be the authorized attempt.",
    )


@dataclass
class _Reviewer:
    """A hermetic reviewer that records the case it was shown and answers from a script."""

    answers: list[Callable[[FrozenCase], SemanticJudgment]]
    cases: list[SemanticCase] = field(default_factory=lambda: [])
    seed: int = 2600

    async def __call__(
        self,
        frozen: FrozenCase,
        findings: tuple[Finding, ...],
        runtime: object | None = None,
        lineage_evaluation: object | None = None,
    ) -> FinalSemanticEvaluation:
        del runtime, lineage_evaluation
        self.cases.append(
            build_semantic_case(
                case_id=protocol_id("cas_", self.seed + len(self.cases)),
                frozen_case=frozen.case,
                dependency_digest=frozen.lease.dependency_digest,
                findings=findings,
                review_context_profile=ReviewContextProfile.GOAL_AWARE,
                review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.GOAL_AWARE),
                policy_id=protocol_id("pvy_", self.seed),
                policy_version="1",
            )
        )
        judgment = self.answers[len(self.cases) - 1](frozen)
        return FinalSemanticEvaluation(
            SemanticStatus.SUCCEEDED,
            SemanticReason.SEMANTIC_COMPLETED,
            judgment=judgment,
            provenance=_provenance(self.seed + 10 * len(self.cases)),
        )


def _challenge_obligation(frozen: FrozenCase) -> SemanticJudgment:
    obligation = next(iter(frozen.case.projection.obligations))
    return SemanticJudgment("challenges_returned", (_challenge(str(obligation)),))


def _records(app: Application) -> tuple[LedgerRecord, ...]:
    runtime = cast(Any, app.runtime)
    ledger, _objects = next(iter(runtime.resources.values()))
    return tuple(ledger._state.records)  # pyright: ignore[reportPrivateUsage]


def _live_projection(app: Application) -> ProjectionState:
    runtime = cast(Any, app.runtime)
    ledger, _objects = next(iter(runtime.resources.values()))
    return cast(ProjectionState, ledger._state.projection)  # pyright: ignore[reportPrivateUsage]


@dataclass(frozen=True, slots=True)
class _Session:
    app: Application
    session_id: str
    writer_id: str
    obligation_id: str


async def _session(reviewer: _Reviewer, seed: int) -> tuple[_Session, JsonValue]:
    app, _policy = await build_projection_application("optional", seed=seed, max_findings=5)
    app = replace(app, semantic_evaluator=reviewer)
    started = await app.start(start_request(seed, title="Converging review dialogue"))
    obligation_id = protocol_id("obl_", seed + 1)
    obligation_event = protocol_id("evt_", seed + 2)
    published = await app.publish_work(
        PublishWorkRequest.model_validate(
            {
                **request_base(protocol_id("req_", seed + 3)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": frontier_json(started.frontier),
                "event_drafts": [
                    {
                        "event_id": obligation_event,
                        "schema": {"name": "obligation_published", "version": "1.0.0"},
                        "occurred_at": "2026-09-28T12:00:00.000Z",
                        "causal_parents": [],
                        "payload": {
                            "obligation_id": obligation_id,
                            "description": "Verify the stencil change with llvmlite 0.46.0.",
                            "acceptance_criteria": "Stencil tests pass on llvmlite 0.46.0.",
                            "evidence_expectation": "A recorded test result.",
                            "requested_items": [{"item_kind": "change", "value": "stencil"}],
                            "status": "open",
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    }
                ],
            }
        )
    )
    session = _Session(app, started.session_id, started.writer_id, obligation_id)
    return session, frontier_json(published.result_frontier)


async def _check(session: _Session, frontier: JsonValue, seed: int) -> CheckCommitResult:
    checked = await session.app.check(
        CheckRequest.model_validate(
            {
                **request_base(protocol_id("req_", seed)),
                "session_id": session.session_id,
                "writer_id": session.writer_id,
                "expected_frontier": frontier,
                "mode": "semantic_required",
                "max_findings": "5",
            }
        )
    )
    assert type(checked) is CheckCommitResult, f"unexpected nonterminal check: {type(checked)}"
    return checked


def _semantic(checked: CheckCommitResult) -> Finding:
    return next(
        finding
        for finding in checked.findings
        if finding.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED
    )


async def test_challenge_fields_are_recorded_and_carried_into_the_next_review() -> None:
    seed = 2700
    reviewer = _Reviewer([_challenge_obligation, _challenge_obligation])
    session, frontier = await _session(reviewer, seed)

    first = await _check(session, frontier, seed + 10)
    raised = _semantic(first)
    rows = [
        row
        for row in _records(session.app)
        if row.schema.name == "finding_recorded"
        and isinstance(row.payload, Finding)
        and row.payload.finding_id == raised.finding_id
    ]
    assert [row.schema for row in rows] == [EventSchema("finding_recorded", "1.4.0")]
    recorded = cast(Finding, rows[0].payload)
    assert recorded.challenge is not None
    assert recorded.challenge.discrepancy == _DISCREPANCY
    assert recorded.challenge.requested_next_step == "act"
    # Local findings keep the frozen 1.3.0 shape and bytes.
    assert all(
        row.schema.version == "1.3.0"
        for row in _records(session.app)
        if row.schema.name == "finding_recorded"
        and isinstance(row.payload, Finding)
        and row.payload.origin is FindingOrigin.DETERMINISTIC
    )

    responded = await session.app.respond(
        RespondRequest.model_validate(
            {
                **request_base(protocol_id("req_", seed + 20)),
                "session_id": session.session_id,
                "writer_id": session.writer_id,
                "expected_frontier": frontier_json(first.result_frontier),
                "finding_id": raised.finding_id,
                "finding_frontier": frontier_json(first.result_frontier),
                "disposition": "rejected",
                "reason": "Two exact-version installation attempts already failed on the index.",
            }
        )
    )
    await _check(session, frontier_json(responded.result_frontier), seed + 30)

    second_case = reviewer.cases[1]
    structural = next(
        item for item in second_case.items if item.item_id == f"prior-finding-{raised.finding_id}"
    )
    row = cast(Mapping[str, JsonValue], strict_json_parse(structural.content))
    assert row["challenge_fields"] == "recorded"
    assert row["requested_next_step"] == "act"
    assert cast(Mapping[str, JsonValue], row["response"])["disposition"] == "rejected"
    texts = {item.item_id: item.content.decode("utf-8") for item in second_case.items}
    assert texts[f"prior-finding-discrepancy-{raised.finding_id}"] == _DISCREPANCY
    assert "already failed" in texts[f"prior-finding-response-{raised.finding_id}"]

    # A projection rebuilt from the recorded events reads exactly what the live one holds.
    assert projection_snapshot(replay(_records(session.app))) == projection_snapshot(
        _live_projection(session.app)
    )


async def _repair(session: _Session, frontier: JsonValue, seed: int) -> tuple[str, JsonValue]:
    """Publish the repair and its regression result, as the kea agent did after check 2."""

    action_id = protocol_id("act_", seed)
    result_id = protocol_id("res_", seed + 1)
    action_event = protocol_id("evt_", seed + 2)
    published = await session.app.publish_work(
        PublishWorkRequest.model_validate(
            {
                **request_base(protocol_id("req_", seed + 3)),
                "session_id": session.session_id,
                "writer_id": session.writer_id,
                "expected_frontier": frontier,
                "event_drafts": [
                    {
                        "event_id": action_event,
                        "schema": {"name": "action_recorded", "version": "1.0.0"},
                        "occurred_at": "2026-09-28T12:05:00.000Z",
                        "causal_parents": [],
                        "payload": {
                            "action_id": action_id,
                            "action_kind": "edit",
                            "description": "Encode Map keys with their type before joining.",
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    },
                    {
                        "event_id": protocol_id("evt_", seed + 4),
                        "schema": {"name": "result_recorded", "version": "1.0.0"},
                        "occurred_at": "2026-09-28T12:05:01.000Z",
                        "causal_parents": [action_event],
                        "payload": {
                            "result_id": result_id,
                            "action_id": action_id,
                            "outcome": "success",
                            "summary": "Map keys 1 and '1' now have distinct dependency strings.",
                        },
                        "artifact_refs": [],
                        "evidence_refs": [],
                    },
                ],
            }
        )
    )
    return result_id, frontier_json(published.result_frontier)


def _rule_first_finding(
    verdict: str, *, cite_repair: bool
) -> Callable[[FrozenCase], SemanticJudgment]:
    def answer(frozen: FrozenCase) -> SemanticJudgment:
        projection = frozen.case.projection
        finding = next(
            key
            for key, row in projection.findings.items()
            if row.payload is not None
            and row.payload.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED
        )
        newest = max(projection.results.items(), key=lambda pair: pair[1].source_frontier)[0]
        refs = (str(newest),) if cite_repair else ()
        return SemanticJudgment(
            "insufficient_packet",
            (),
            (PriorFindingVerdict(str(finding), verdict, refs),),  # type: ignore[arg-type]
        )

    return answer


@pytest.mark.parametrize("cite_repair", [True, False])
async def test_a_cited_fixed_ruling_closes_a_repaired_finding_under_an_insufficient_packet(
    cite_repair: bool,
) -> None:
    """kea fnd_866db2dd end to end: raised, repaired with a regression, then ruled fixed.

    The recheck's packet as a whole is ``insufficient_packet``. With the repair cited the finding
    resolves on that check and says why; a ``fixed`` citing nothing newer is only
    ``unassessable``, leaves the finding open, and is disclosed on the check.
    """

    seed = 2800 + (0 if cite_repair else 100)
    reviewer = _Reviewer(
        [_challenge_obligation, _rule_first_finding("fixed", cite_repair=cite_repair)]
    )
    session, frontier = await _session(reviewer, seed)
    first = await _check(session, frontier, seed + 10)
    raised = _semantic(first)
    _result, repaired = await _repair(session, frontier_json(first.result_frontier), seed + 20)
    second = await _check(session, repaired, seed + 40)

    assert "semantic_packet_insufficient" in second.coverage.known_gaps
    check_rows = [row for row in _records(session.app) if row.schema.name == "check_recorded"]
    recorded = cast(CheckRecordedPayload, check_rows[-1].payload)
    live = _live_projection(session.app)
    resolved_by = live.findings[raised.finding_id].resolved_by_check_event_id
    if cite_repair:
        assert check_rows[-1].schema == EventSchema("check_recorded", "1.4.0")
        assert [item.verdict for item in recorded.prior_finding_verdicts] == ["fixed"]
        assert resolved_by == check_rows[-1].event_id
        explanation = finding_resolution_explanation(live, raised.finding_id, _records(session.app))
        assert "ruled it fixed" in explanation
    else:
        assert [item.verdict for item in recorded.prior_finding_verdicts] == ["unassessable"]
        assert "semantic_prior_verdicts_unsupported" in second.coverage.known_gaps
        assert resolved_by is None
    assert projection_snapshot(replay(_records(session.app))) == projection_snapshot(live)
