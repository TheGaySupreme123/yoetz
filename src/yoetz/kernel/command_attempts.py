"""Evidence-bounded command reconciliation; no shell parsing, execution, or live reads."""

from __future__ import annotations

from yoetz.domain.events import LedgerRecord, is_observation_authored
from yoetz.domain.values import ActionId, EventId, ObligationId, ResultId
from yoetz.kernel.projections import ProjectionState
from yoetz.protocol.models import StatusCommandAttemptModel


def command_attempts(
    state: ProjectionState, records: tuple[LedgerRecord, ...], obligation_id: ObligationId
) -> tuple[StatusCommandAttemptModel, ...]:
    """Compare only explicitly linked observations; unrelated commands prove nothing.

    A cooperative action may name an observed action event as a causal parent. An obligation's
    resolution result may name an observed action. Both are explicit relevance assertions, not
    temporal correlation. Service-stamped authorship supplies the independent observation fact.
    Missing, omitted, ambiguous, or redacted command bytes stay unknown. Exact bytes are the only
    matching rule: wrappers and formatting are not assumed equivalent.
    """

    obligation = state.obligations[obligation_id].payload
    if obligation is None:
        return ()
    by_event = {
        row.event_id: row for row in records if row.ledger.ingestion_sequence <= state.frontier
    }
    output: list[StatusCommandAttemptModel] = []
    for index, requested in enumerate(obligation.requested_items):
        if requested.item_kind.value != "command":
            continue
        assertions = [
            record
            for record in state.actions.values()
            if record.payload is not None
            and requested.value in record.payload.attempted_items
            and (
                not record.payload.obligation_refs
                or obligation_id in record.payload.obligation_refs
            )
        ]
        linked: set[EventId] = set()
        for assertion in assertions:
            event = by_event.get(assertion.source_event_id)
            if event is not None:
                linked.update(event.causal_parents)
                if is_observation_authored(event):
                    linked.add(event.event_id)
        for ref in obligation.resolution_evidence_refs:
            if not ref.startswith("res_"):
                continue
            result = state.results.get(ResultId(ref))
            if result is not None and result.payload is not None:
                action = state.actions.get(result.payload.action_id)
                if action is not None:
                    linked.add(action.source_event_id)
        observed = [
            row
            for key in sorted(linked)
            if (row := by_event.get(key)) is not None
            and is_observation_authored(row)
            and row.schema.name == "action_recorded"
        ]
        commands: set[str | None] = set()
        for row in observed:
            key = row.projection_locator.logical_key
            action = state.actions.get(ActionId(key)) if key is not None else None
            if action is None or action.payload is None or action.source_event_id != row.event_id:
                commands.add(None)
            elif action.payload.action_kind.value == "command":
                commands.add(action.payload.command)
            else:
                commands.add(None)
        unavailable = not commands or any(
            value is None or value.startswith("omitted:") for value in commands
        )
        relation = "unknown"
        if not unavailable and len(commands) == 1:
            relation = (
                "matching_observed_attempt"
                if requested.value in commands
                else "asserted_observed_mismatch"
            )
        # Without an assertion this is evidence discovery, not an asserted/observed disagreement.
        if not assertions and relation == "asserted_observed_mismatch":
            relation = "unknown"
        if len(assertions) > 64 or len(observed) > 64:
            relation = "unknown"
        output.append(
            StatusCommandAttemptModel.model_validate(
                {
                    "requested_item_index": str(index),
                    "relation": relation,
                    "asserted_action_ids": sorted(
                        {
                            str(row.payload.action_id)
                            for row in assertions
                            if row.payload is not None
                        }
                    )[:64],
                    "observed_event_ids": sorted({str(row.event_id) for row in observed})[:64],
                }
            )
        )
    return tuple(output)


def closure_command_gaps(
    state: ProjectionState, records: tuple[LedgerRecord, ...]
) -> tuple[tuple[ObligationId, str], ...]:
    """Completion-time limits for asserted commands, without asserting they never ran."""
    from yoetz.kernel.claims import effective_claim_items

    selected = {
        ref
        for _, claim in effective_claim_items(state)
        if claim.payload is not None and claim.payload.claim_kind.value == "completion"
        for ref in claim.payload.obligation_refs
    }
    selected.update(
        key
        for key, row in state.obligations.items()
        if row.payload is not None and row.payload.status.value == "resolved"
    )
    from yoetz.kernel.plan_scope import current_plan_scope

    scope = current_plan_scope(state.plans, state.coverage_gaps)
    selected.intersection_update(scope.effective_obligation_refs or ())
    gaps: set[tuple[ObligationId, str]] = set()
    for key in sorted(selected):
        if key not in state.obligations:
            continue
        for attempt in command_attempts(state, records, key):
            if attempt.relation == "unknown":
                gaps.add((key, "command_attempt_uncorroborated"))
            elif attempt.relation == "asserted_observed_mismatch":
                gaps.add((key, "command_attempt_mismatch"))
    return tuple(sorted(gaps))
