"""Task facts from hook observations and the ledger (issue #977)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from builders.observed_runs import INSTALLATION_KEY, ObservedLedger
from yoetz.domain.events import (
    ActionKind,
    ActionRecordedPayload,
    DecisionRecordedPayload,
    EventSchema,
    ObligationPublishedPayload,
    ObligationStatus,
    PlanPublishedPayload,
    RequestedItem,
    RequestedItemKind,
    ResultOutcome,
)
from yoetz.domain.values import ObligationId, action_id, actor_id, obligation_id
from yoetz.kernel.closure_readiness import (
    GAP_CLASSIFICATION,
    GapClass,
    closure_readiness_facts,
    derive_closure_readiness,
)
from yoetz.kernel.observed_failures import (
    observed_action_description,
    observed_action_runner_class,
    observed_action_runtime_tokens,
    observed_action_tool,
    observed_event_ids_from_records,
)
from yoetz.kernel.reducers import replay
from yoetz.kernel.task_facts import (
    AGENT_ACTIONABLE_TASK_FACT_GAPS,
    EDITED_AFTER_LAST_VERIFICATION_GAP,
    INSTALL_INTO_PRIVATE_ENV_GAP,
    INSTALL_INTO_YOETZ_RUNTIME_GAP,
    OBLIGATION_BLOCKED_GAP,
    PLANNED_VERIFICATION_FAILED_GAP,
    PLANNED_VERIFICATION_NOT_OBSERVED_GAP,
    PLANNED_VERIFICATION_OUTCOME_UNKNOWN_GAP,
    PLANNED_VERIFICATION_STALE_GAP,
    PLANNED_VERIFICATION_UNOBSERVABLE_GAP,
    REQUESTED_OUTPUT_GIT_IGNORED_GAP,
    REQUESTED_OUTPUT_IGNORED_BY_REPOSITORY_GAP,
    REQUESTED_OUTPUT_OUTSIDE_WORKSPACE_GAP,
    REQUESTED_OUTPUT_UNCHANGED_GAP,
    REQUESTED_OUTPUT_UNVERIFIED_GAP,
    STANDING_TASK_FACT_GAPS,
    TASK_FACT_GAPS,
    VERIFICATION_ONLY_AS_ROOT_GAP,
    WRITE_OUTSIDE_WORKSPACE_GAP,
    RequestedOutputState,
    acting_started,
    blocked_obligations,
    cooperative_event_ids_from_records,
    register_command_identity_key,
    requested_output_facts,
    task_fact_signals,
)

_OBLIGATION = obligation_id("obl_00000000-0000-4000-8000-000000000001")
_OTHER = obligation_id("obl_00000000-0000-4000-8000-000000000002")
_OBLIGATION_SCHEMA = EventSchema("obligation_published", "1.0.0")
_PLAN_SCHEMA = EventSchema("plan_published", "1.0.0")
_DECISION_SCHEMA = EventSchema("decision_recorded", "1.0.0")
_ACTION_SCHEMA = EventSchema("action_recorded", "1.0.0")


@pytest.fixture(autouse=True)
def installation_key() -> Iterator[None]:
    register_command_identity_key(INSTALLATION_KEY)
    yield
    register_command_identity_key(None)


def _planned(
    ledger: ObservedLedger,
    *items: RequestedItem,
    obligation: ObligationId = _OBLIGATION,
    status: ObligationStatus = ObligationStatus.OPEN,
) -> None:
    ledger.append(
        _OBLIGATION_SCHEMA,
        ObligationPublishedPayload(
            obligation_id=obligation,
            description="Synthetic obligation",
            evidence_expectation="Observed run",
            status=status,
            requested_items=items,
        ),
        observed=False,
    )


def _plan(ledger: ObservedLedger, *obligations: ObligationId) -> None:
    ledger.append(
        _PLAN_SCHEMA,
        PlanPublishedPayload(1, "Plan", tuple(sorted(obligations))),
        observed=False,
    )


def _command(value: str) -> RequestedItem:
    return RequestedItem(RequestedItemKind.COMMAND, value)


def _codes(ledger: ObservedLedger) -> set[str]:
    prefix = ledger.prefix
    return set(task_fact_signals(replay(prefix), prefix).codes)


def _markers(ledger: ObservedLedger) -> set[tuple[str, ObligationId | None]]:
    prefix = ledger.prefix
    return set(task_fact_signals(replay(prefix), prefix).markers)


def test_every_task_fact_code_is_classified_once() -> None:
    assert AGENT_ACTIONABLE_TASK_FACT_GAPS.isdisjoint(STANDING_TASK_FACT_GAPS)
    for code in AGENT_ACTIONABLE_TASK_FACT_GAPS:
        assert GAP_CLASSIFICATION[code] is GapClass.AGENT_ACTIONABLE
    for code in STANDING_TASK_FACT_GAPS:
        assert GAP_CLASSIFICATION[code] is GapClass.STANDING_LIMITATION
    assert TASK_FACT_GAPS == AGENT_ACTIONABLE_TASK_FACT_GAPS | STANDING_TASK_FACT_GAPS


def test_planned_verification_never_observed_when_another_command_ran() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q tests/unit"))
    _plan(ledger, _OBLIGATION)
    ledger.passes("pytest -q")

    assert (PLANNED_VERIFICATION_NOT_OBSERVED_GAP, _OBLIGATION) in _markers(ledger)


def test_planned_verification_matches_after_light_normalization_only() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest   -q"))
    _plan(ledger, _OBLIGATION)
    ledger.passes("pytest -q")

    assert not {
        PLANNED_VERIFICATION_NOT_OBSERVED_GAP,
        PLANNED_VERIFICATION_FAILED_GAP,
        PLANNED_VERIFICATION_STALE_GAP,
    } & _codes(ledger)


def test_planned_verification_failed_then_repaired_by_a_passing_rerun() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q"))
    _plan(ledger, _OBLIGATION)
    ledger.fail("pytest -q")
    assert (PLANNED_VERIFICATION_FAILED_GAP, _OBLIGATION) in _markers(ledger)

    ledger.passes("pytest -q")
    assert PLANNED_VERIFICATION_FAILED_GAP not in _codes(ledger)


def test_planned_verification_stale_after_a_later_edit_until_rerun() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q"))
    _plan(ledger, _OBLIGATION)
    ledger.passes("pytest -q")
    ledger.edit()
    assert (PLANNED_VERIFICATION_STALE_GAP, _OBLIGATION) in _markers(ledger)

    ledger.passes("pytest -q")
    assert PLANNED_VERIFICATION_STALE_GAP not in _codes(ledger)


def test_failed_edit_does_not_make_verification_stale() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q"))
    _plan(ledger, _OBLIGATION)
    ledger.passes("pytest -q")
    ledger.edit(ResultOutcome.FAILURE)

    assert PLANNED_VERIFICATION_STALE_GAP not in _codes(ledger)


def test_unknown_outcome_is_disclosed_not_gated() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("bash run.sh"))
    _plan(ledger, _OBLIGATION)
    ledger.run("bash run.sh", ResultOutcome.UNKNOWN)

    codes = _codes(ledger)
    assert PLANNED_VERIFICATION_OUTCOME_UNKNOWN_GAP in codes
    assert PLANNED_VERIFICATION_NOT_OBSERVED_GAP not in codes
    assert PLANNED_VERIFICATION_FAILED_GAP not in codes


def test_without_the_installation_key_no_planned_fact_is_claimed() -> None:
    register_command_identity_key(None)
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q"))
    _plan(ledger, _OBLIGATION)
    ledger.passes("pytest -k other")

    assert not any(code.startswith("planned_verification") for code in _codes(ledger))


def test_without_keyed_observed_runs_the_match_is_unobservable() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q"))
    _plan(ledger, _OBLIGATION)
    ledger.run(None, ResultOutcome.SUCCESS, exit_status=0)  # omitted:structural legacy row

    assert (PLANNED_VERIFICATION_UNOBSERVABLE_GAP, _OBLIGATION) in _markers(ledger)
    assert PLANNED_VERIFICATION_NOT_OBSERVED_GAP not in _codes(ledger)


def test_cooperative_runs_never_count_as_observed_verification() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q"))
    _plan(ledger, _OBLIGATION)
    ledger.passes("ls")  # keyed observed command, so matching is observable
    ledger.run("pytest -q", ResultOutcome.SUCCESS, exit_status=0, observed=False)

    assert (PLANNED_VERIFICATION_NOT_OBSERVED_GAP, _OBLIGATION) in _markers(ledger)


def test_edited_after_last_verification_needs_a_prior_verification_run() -> None:
    ledger = ObservedLedger()
    ledger.edit()
    assert EDITED_AFTER_LAST_VERIFICATION_GAP not in _codes(ledger)

    ledger.passes("pytest -q")
    assert EDITED_AFTER_LAST_VERIFICATION_GAP not in _codes(ledger)
    ledger.edit()
    assert EDITED_AFTER_LAST_VERIFICATION_GAP in _codes(ledger)
    ledger.fail("pytest -q")
    assert EDITED_AFTER_LAST_VERIFICATION_GAP not in _codes(ledger)


def test_exploration_runs_are_not_verification_runs() -> None:
    ledger = ObservedLedger()
    ledger.passes("pytest -q")
    ledger.edit()
    ledger.passes("ls -la")
    assert EDITED_AFTER_LAST_VERIFICATION_GAP in _codes(ledger)


def test_runtime_facts_from_observed_descriptions() -> None:
    ledger = ObservedLedger()
    ledger.run(
        "pip install numpy",
        ResultOutcome.SUCCESS,
        exit_status=0,
        install_target="yoetz_runtime",
        effective_user="root",
    )
    ledger.run(
        "pip install pandas",
        ResultOutcome.SUCCESS,
        exit_status=0,
        install_target="private_env",
    )
    ledger.edit(write_outside_workspace=True)
    codes = _codes(ledger)
    assert {
        INSTALL_INTO_YOETZ_RUNTIME_GAP,
        INSTALL_INTO_PRIVATE_ENV_GAP,
        WRITE_OUTSIDE_WORKSPACE_GAP,
    } <= codes
    assert VERIFICATION_ONLY_AS_ROOT_GAP not in codes


def test_workspace_and_system_installs_are_not_reported() -> None:
    ledger = ObservedLedger()
    for target in ("workspace_env", "system", "unresolved"):
        ledger.run("pip install x", ResultOutcome.SUCCESS, exit_status=0, install_target=target)
    assert not {INSTALL_INTO_YOETZ_RUNTIME_GAP, INSTALL_INTO_PRIVATE_ENV_GAP} & _codes(ledger)


def test_verification_only_as_root() -> None:
    ledger = ObservedLedger()
    ledger.run("pytest -q", ResultOutcome.SUCCESS, exit_status=0, effective_user="root")
    assert VERIFICATION_ONLY_AS_ROOT_GAP in _codes(ledger)
    ledger.run("pytest -q", ResultOutcome.SUCCESS, exit_status=0, effective_user="non_root")
    assert VERIFICATION_ONLY_AS_ROOT_GAP not in _codes(ledger)


def test_description_suffixes_round_trip_with_tool_and_runner() -> None:
    description = observed_action_description(
        "Observed command via Codex hook",
        "exec_command",
        "test",
        install_target="yoetz_runtime",
        write_outside_workspace=True,
        effective_user="root",
    )
    assert observed_action_tool(description) == "exec_command"
    assert observed_action_runner_class(description) == "test"
    assert observed_action_runtime_tokens(description) == frozenset(
        {"install yoetz_runtime", "write outside_workspace", "user root"}
    )
    plain = observed_action_description("Observed edit", "apply_patch", None)
    assert observed_action_runtime_tokens(plain) == frozenset()
    assert observed_action_tool(plain) == "apply_patch"


def _decision(ledger: ObservedLedger, statement: str, *obligations: ObligationId) -> None:
    ledger.append(
        _DECISION_SCHEMA,
        DecisionRecordedPayload(
            statement=statement,
            rationale="The deployment credential is held by the operator.",
            authority=actor_id("agt_codex"),
            affected_obligation_ids=tuple(sorted(obligations)),
        ),
        observed=False,
    )


def test_blocker_decision_is_honoured_only_for_closed_kinds() -> None:
    ledger = ObservedLedger()
    _planned(ledger, _command("deploy --prod"))
    _planned(ledger, obligation=_OTHER)
    _plan(ledger, _OBLIGATION, _OTHER)
    _decision(ledger, "yoetz-blocker:data_not_found", _OTHER)
    _decision(ledger, "Blocked.\nyoetz-blocker:credential", _OBLIGATION)
    prefix = ledger.prefix
    projection = replay(prefix)

    assert dict(blocked_obligations(projection)) == {_OBLIGATION: "credential"}
    markers = set(task_fact_signals(projection, prefix).markers)
    assert (OBLIGATION_BLOCKED_GAP, _OBLIGATION) in markers
    assert (OBLIGATION_BLOCKED_GAP, _OTHER) not in markers
    # A blocked obligation's planned verification is not reported as missing.
    assert not any(code.startswith("planned_verification") for code, _ in markers)


def test_readiness_turns_fully_blocked_open_obligations_into_a_disclosure() -> None:
    ledger = ObservedLedger()
    _planned(ledger, obligation=_OBLIGATION)
    _planned(ledger, obligation=_OTHER)
    _plan(ledger, _OBLIGATION, _OTHER)
    _decision(ledger, "yoetz-blocker:authority", _OBLIGATION)
    prefix = ledger.prefix
    facts = closure_readiness_facts(replay(prefix), prefix)
    partly = derive_closure_readiness(
        ("obligations_open",), (), facts, semantic_review_required=False
    )
    assert "obligations_open" in partly.agent_actionable

    _decision(ledger, "yoetz-blocker:dependency_unavailable", _OTHER)
    prefix = ledger.prefix
    facts = closure_readiness_facts(replay(prefix), prefix)
    blocked = derive_closure_readiness(
        ("obligations_open",), (), facts, semantic_review_required=False
    )
    assert "obligations_open" not in blocked.agent_actionable
    assert OBLIGATION_BLOCKED_GAP in blocked.standing_limitations
    # Only the missing check remains: the blocked obligations are not agent work.
    assert blocked.agent_actionable == ("check_not_recorded",)


def test_acting_starts_with_an_edit_a_published_action_or_a_verification_run() -> None:
    def started(ledger: ObservedLedger) -> bool:
        prefix = ledger.prefix
        return acting_started(
            replay(prefix),
            observed_event_ids_from_records(prefix),
            cooperative_event_ids_from_records(prefix),
        )

    ledger = ObservedLedger()
    _planned(ledger, RequestedItem(RequestedItemKind.FILE, "out/report.json"))
    _plan(ledger, _OBLIGATION)
    ledger.passes("ls")
    ledger.passes("git status")
    ledger.passes("python probe.py")  # an unclassified investigation run is not acting
    assert not started(ledger)
    ledger.passes("pytest -q")
    assert started(ledger)

    published = ObservedLedger()
    _planned(published, RequestedItem(RequestedItemKind.FILE, "out/report.json"))
    published.append(
        _ACTION_SCHEMA,
        ActionRecordedPayload(
            action_id("act_00000000-0000-4000-8000-000000000777"),
            ActionKind.RESEARCH,
            "Read the inputs.",
        ),
        observed=False,
    )
    assert started(published)

    edited = ObservedLedger()
    edited.edit()
    assert started(edited)


def test_requested_output_facts_split_absent_from_gap_markers() -> None:
    ledger = ObservedLedger()
    _planned(ledger, RequestedItem(RequestedItemKind.FILE, "x"))
    _plan(ledger, _OBLIGATION)
    projection = replay(ledger.prefix)
    states = {
        (_OBLIGATION, 0): RequestedOutputState("inside", exists=False),
        (_OBLIGATION, 1): RequestedOutputState("inside", exists=False, deleted=True),
        (_OBLIGATION, 2): RequestedOutputState(
            "inside", exists=True, ignored=True, ignored_by_task=True
        ),
        (_OBLIGATION, 7): RequestedOutputState("inside", exists=True, ignored=True),
        (_OBLIGATION, 3): RequestedOutputState("inside", exists=True, ignored=False, changed=False),
        (_OBLIGATION, 4): RequestedOutputState("outside"),
        (_OBLIGATION, 5): RequestedOutputState("unverified"),
        (_OBLIGATION, 6): RequestedOutputState("inside", exists=True, ignored=False, changed=True),
    }
    absent, markers = requested_output_facts(projection, states)
    assert absent == ((_OBLIGATION, 0),)
    assert {code for code, _ in markers} == {
        REQUESTED_OUTPUT_GIT_IGNORED_GAP,
        REQUESTED_OUTPUT_IGNORED_BY_REPOSITORY_GAP,
        REQUESTED_OUTPUT_UNCHANGED_GAP,
        REQUESTED_OUTPUT_OUTSIDE_WORKSPACE_GAP,
        REQUESTED_OUTPUT_UNVERIFIED_GAP,
    }

    _decision(ledger, "yoetz-blocker:consent", _OBLIGATION)
    blocked_absent, blocked_markers = requested_output_facts(replay(ledger.prefix), states)
    assert blocked_absent == ()
    assert blocked_markers == ()


def test_every_task_fact_code_has_one_fixed_sentence() -> None:
    from yoetz.domain.receipts import TASK_FACT_GAP_SENTENCES, check_time_change_gap_sentence

    assert set(TASK_FACT_GAP_SENTENCES) == set(TASK_FACT_GAPS)
    for code in TASK_FACT_GAPS:
        assert check_time_change_gap_sentence(code) == TASK_FACT_GAP_SENTENCES[code]


def test_a_started_run_without_a_result_is_unknown_not_unobserved() -> None:
    from builders.observed_runs import command_identity_for
    from yoetz.domain.values import action_id as make_action_id
    from yoetz.kernel.observed_failures import observed_action_description

    ledger = ObservedLedger()
    _planned(ledger, _command("pytest -q"))
    _plan(ledger, _OBLIGATION)
    ledger.append(
        _ACTION_SCHEMA,
        ActionRecordedPayload(
            make_action_id("act_00000000-0000-4000-8000-000000000555"),
            ActionKind.COMMAND,
            observed_action_description("Observed pending command via Codex hook", "Bash", "test"),
            command=command_identity_for("pytest -q"),
        ),
        observed=True,
    )
    codes = _codes(ledger)
    assert PLANNED_VERIFICATION_NOT_OBSERVED_GAP not in codes
    assert PLANNED_VERIFICATION_OUTCOME_UNKNOWN_GAP in codes
