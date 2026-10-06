"""Replay two TB4 tb4f1 deterministic traces through the task facts and the Stop gate (#977).

The fixtures under ``tests/fixtures/tb4_replay/`` reduce each attempt's ``agent/codex.txt`` and its
exported obligations view to structure: the order of observed commands (runner class, exit
status, closed runtime facts), edits, accepted published actions, checks and the final stop.
Command text survives only where it equals an obligation's requested command. The tests rebuild
the ledger the way the coordinator materializes it, then ask what this build would have told the
agent at each check and at the final Stop.

* pretrain-shard-corruption (deterministic, reward 0): the agent stopped with three open
  obligations after asking an absent user for the original data; its planned verification
  ``bash /app/run_pretrain.sh`` only ever ran wrapped with a redirect into ``/tmp``.
* atrx-vep-crispr (deterministic, reward 0): ``/app/output/mutation.report.json`` was never
  written, yet a published action named the obligation's inputs and the agent stopped.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from builders.observed_runs import INSTALLATION_KEY, ObservedLedger
from yoetz.application.check import CheckScope, run_deterministic_policies
from yoetz.cli.closure_gate import BLOCKER_RECHECK_ITEM, closure_gate_from_readiness
from yoetz.domain.events import (
    ActionKind,
    ActionRecordedPayload,
    EventSchema,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    RequestedItem,
    RequestedItemKind,
    ResultOutcome,
    ResultRecordedPayload,
)
from yoetz.domain.findings import FindingKind
from yoetz.domain.values import ObligationId, action_id, obligation_id, result_id
from yoetz.kernel.closure_readiness import closure_readiness_facts, derive_closure_readiness
from yoetz.kernel.deterministic_checks import (
    REQUESTED_OUTPUT_ABSENT_FACT,
    CaseAvailabilityFacts,
    build_deterministic_case,
)
from yoetz.kernel.policies.work_integrity import work_integrity_findings
from yoetz.kernel.reducers import replay
from yoetz.kernel.task_facts import (
    PLANNED_VERIFICATION_NOT_OBSERVED_GAP,
    WRITE_OUTSIDE_WORKSPACE_GAP,
    RequestedOutputState,
    open_effective_obligations,
    register_command_identity_key,
    requested_output_facts,
    task_fact_signals,
)
from yoetz.protocol.policy_packs import current_policy_pack

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "tb4_replay"
_PACKS = (current_policy_pack("research-evidence"), current_policy_pack("work-integrity"))


@pytest.fixture(autouse=True)
def installation_key() -> Iterator[None]:
    register_command_identity_key(INSTALLATION_KEY)
    yield
    register_command_identity_key(None)


@dataclass
class _Replay:
    ledger: ObservedLedger
    obligations: tuple[ObligationId, ...]
    check_findings: list[tuple[FindingKind, ...]]


def _outcome(exit_status: int | None) -> ResultOutcome:
    if exit_status is None:
        return ResultOutcome.UNKNOWN
    return ResultOutcome.SUCCESS if exit_status == 0 else ResultOutcome.FAILURE


def _requested(item: dict[str, str]) -> RequestedItem:
    return RequestedItem(RequestedItemKind(item["item_kind"]), item["value"])


def _kinds(ledger: ObservedLedger) -> tuple[FindingKind, ...]:
    records = ledger.prefix
    case = build_deterministic_case(replay(records), records, CaseAvailabilityFacts())
    assessments, _executions = run_deterministic_policies(case, CheckScope((), ()), _PACKS)
    return tuple(item.candidate.kind for item in assessments)


def _replay(name: str) -> tuple[_Replay, dict[str, Any]]:
    fixture: dict[str, Any] = json.loads((_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    ledger = ObservedLedger()
    ids = tuple(obligation_id(item["obligation_id"]) for item in fixture["obligations"])
    checks: list[tuple[FindingKind, ...]] = []
    actions = 0
    for event in fixture["events"]:
        kind = event["kind"]
        if kind == "plan":
            for item in fixture["obligations"]:
                ledger.append(
                    EventSchema("obligation_published", "1.0.0"),
                    ObligationPublishedPayload(
                        obligation_id=obligation_id(item["obligation_id"]),
                        description="Replayed obligation",
                        evidence_expectation="Replayed",
                        status=ObligationStatus.OPEN,
                        requested_items=tuple(
                            _requested(value) for value in item["requested_items"]
                        ),
                    ),
                    observed=False,
                )
            ledger.append(
                EventSchema("plan_published", "1.0.0"),
                PlanPublishedPayload(1, "Replayed plan", tuple(sorted(ids))),
                observed=False,
            )
        elif kind == "command":
            ledger.run(
                event["planned_command"] or f"replayed-command-{event['event']}",
                _outcome(event["exit_status"]),
                exit_status=event["exit_status"],
                runner=event["runner"],
                write_outside_workspace=event["write_outside_workspace"],
                install_target=event["install_target"],
            )
        elif kind == "edit":
            ledger.edit(write_outside_workspace=event["outside_workspace"])
        elif kind == "published_action":
            actions += 1
            ledger.append(
                EventSchema("action_recorded", "1.0.0"),
                ActionRecordedPayload(
                    action_id(f"act_00000000-0000-4000-8000-{900000 + actions:012d}"),
                    ActionKind.OTHER,
                    "Replayed published action",
                    obligation_refs=tuple(
                        sorted(obligation_id(ref) for ref in event["obligation_refs"])
                    ),
                    attempted_items=tuple(sorted(set(event["attempted_items"]))),
                ),
                observed=False,
            )
            ledger.append(
                EventSchema("result_recorded", "1.0.0"),
                ResultRecordedPayload(
                    result_id(f"res_00000000-0000-4000-8000-{900000 + actions:012d}"),
                    action_id(f"act_00000000-0000-4000-8000-{900000 + actions:012d}"),
                    ResultOutcome.PARTIAL,
                ),
                observed=False,
            )
        elif kind == "check":
            checks.append(_kinds(ledger))
        elif kind == "receipt":
            # The exported end state: obligations the agent resolved are resolved here too.
            ledger.append(
                EventSchema("action_recorded", "1.0.0"),
                ActionRecordedPayload(
                    action_id("act_00000000-0000-4000-8000-000000999999"),
                    ActionKind.OTHER,
                    "Replayed resolution support",
                ),
                observed=False,
            )
            support = result_id("res_00000000-0000-4000-8000-000000999999")
            ledger.append(
                EventSchema("result_recorded", "1.0.0"),
                ResultRecordedPayload(
                    support,
                    action_id("act_00000000-0000-4000-8000-000000999999"),
                    ResultOutcome.SUCCESS,
                ),
                observed=False,
            )
            for item in fixture["obligations"]:
                if item["status"] != "resolved":
                    continue
                ledger.append(
                    EventSchema("obligation_published", "1.0.0"),
                    ObligationPublishedPayload(
                        obligation_id=obligation_id(item["obligation_id"]),
                        description="Replayed obligation",
                        evidence_expectation="Replayed",
                        status=ObligationStatus.RESOLVED,
                        requested_items=tuple(
                            _requested(value) for value in item["requested_items"]
                        ),
                        resolution_evidence_refs=(support,),
                    ),
                    observed=False,
                )
    return _Replay(ledger, ids, checks), fixture


def _stop_gate(
    replayed: _Replay,
    output_states: dict[tuple[ObligationId, int], RequestedOutputState],
    *,
    reasked: frozenset[str] = frozenset(),
) -> tuple[Any, Any]:
    records = replayed.ledger.prefix
    projection = replay(records)
    signals = task_fact_signals(projection, records)
    absent, markers = requested_output_facts(projection, output_states)
    case = build_deterministic_case(projection, records, CaseAvailabilityFacts())
    findings = work_integrity_findings(case, absent_outputs=frozenset(absent))
    blocking_findings = [
        item
        for item in findings
        if item.candidate.kind is FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED
        and any(
            fact.fact_code == REQUESTED_OUTPUT_ABSENT_FACT for fact in item.basis.observed_facts
        )
    ]
    open_obligations = open_effective_obligations(projection)
    conditions: list[str] = []
    if open_obligations:
        conditions.append("obligations_open")
    if blocking_findings:
        conditions.append("receipt_findings_unresolved")
    facts = closure_readiness_facts(projection, records)
    readiness = derive_closure_readiness(
        conditions,
        (*signals.codes, *(code for code, _ in markers)),
        facts,
        semantic_review_required=False,
    )
    view = SimpleNamespace(
        state=readiness.state,
        agent_actionable=readiness.agent_actionable,
        open_obligation_count=str(len(open_obligations)),
        receipt_blocking_finding_count=str(len(blocking_findings)),
        blocked_obligations=tuple(
            SimpleNamespace(obligation_id=obligation, blocker_kind=kind, decision_event_id=event)
            for obligation, kind, event in facts.blocked_obligation_details
        ),
    )
    gate = closure_gate_from_readiness(
        view,
        frontier_sequence=str(projection.frontier),
        frontier_digest=projection.head_digest,
        observation_pending=False,
        reasked_blockers=reasked,
    )
    return gate, signals


def test_pretrain_shard_corruption_det_would_have_been_continued_at_stop() -> None:
    replayed, fixture = _replay("pretrain_shard_corruption_det")

    # Plan-stage noise is gone: the first check ran before any edit or published action.
    assert FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED not in replayed.check_findings[0]

    states = {
        (obligation_id(item["obligation_id"]), index): RequestedOutputState(
            "inside", exists=True, ignored=False, changed=True
        )
        for item in fixture["obligations"]
        for index, value in enumerate(item["requested_items"])
        if value["item_kind"] == "file"
    }
    gate, signals = _stop_gate(replayed, states)

    # Its planned verification only ever ran wrapped with a redirect, so the exact command was
    # never observed; the redirect itself wrote outside the workspace.
    assert PLANNED_VERIFICATION_NOT_OBSERVED_GAP in signals.codes
    assert WRITE_OUTSIDE_WORKSPACE_GAP in signals.codes
    assert gate is not None
    assert gate.items == ("obligations_open", PLANNED_VERIFICATION_NOT_OBSERVED_GAP)
    # The export recorded open_obligation_count 3 at session end.
    assert "Remaining: 3 open obligation(s)" in gate.text
    assert "yoetz-blocker:" in gate.text


def test_atrx_vep_crispr_det_missing_report_blocks_the_receipt_and_continues_the_agent() -> None:
    replayed, fixture = _replay("atrx_vep_crispr_det")

    assert FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED not in replayed.check_findings[0]
    states: dict[tuple[ObligationId, int], RequestedOutputState] = {}
    for item in fixture["obligations"]:
        for index, value in enumerate(item["requested_items"]):
            if value["item_kind"] != "file":
                continue
            written = not value["value"].startswith("/app/output/")
            states[(obligation_id(item["obligation_id"]), index)] = RequestedOutputState(
                "inside", exists=written, ignored=False, changed=False if written else None
            )
    gate, _signals = _stop_gate(replayed, states)

    assert gate is not None
    assert gate.items == ("obligations_open", "receipt_findings_unresolved")
    # The export recorded open_obligation_count 5 at session end.
    assert "Remaining: 5 open obligation(s); 1 receipt-blocking finding(s)" in gate.text


def _declare_blocker(replayed: _Replay, rationale: str) -> tuple[ObligationId, ...]:
    from yoetz.domain.events import DecisionRecordedPayload
    from yoetz.domain.values import actor_id

    projection = replay(replayed.ledger.prefix)
    open_obligations = tuple(sorted(open_effective_obligations(projection)))
    replayed.ledger.append(
        EventSchema("decision_recorded", "1.0.0"),
        DecisionRecordedPayload(
            statement="yoetz-blocker:dependency_unavailable",
            rationale=rationale,
            authority=actor_id("agt_codex"),
            affected_obligation_ids=open_obligations,
        ),
        observed=False,
    )
    return open_obligations


def test_tb4v1_pretrain_blocker_for_a_missing_snapshot_is_rechecked_once() -> None:
    """tb4v1: the agent declared ``dependency_unavailable`` for "no original snapshot found"
    although the task said the data was recoverable. Yoetz cannot judge that claim, so the next
    Stop re-asks it once, naming each obligation and the claimed kind."""

    replayed, _fixture = _replay("pretrain_shard_corruption_det")
    blocked = _declare_blocker(replayed, "No original snapshot found.")
    gate, _signals = _stop_gate(replayed, {})

    assert gate is not None
    assert gate.items == (BLOCKER_RECHECK_ITEM,)
    for obligation in blocked:
        assert f"{obligation} (dependency_unavailable)" in gate.text
    assert "Missing or inconsistent data the task says is recoverable" in gate.text
    assert "is not a blocker" in gate.text


def test_a_genuine_blocker_lets_pretrain_stop_after_one_recheck() -> None:
    replayed, _fixture = _replay("pretrain_shard_corruption_det")
    _declare_blocker(replayed, "Replay: every open obligation declared blocked.")
    first, _signals = _stop_gate(replayed, {})
    assert first is not None
    # The agent stops again without changing anything: the blocker is honoured, never a loop.
    second, _signals = _stop_gate(replayed, {}, reasked=frozenset(first.blocker_keys))
    assert second is None
