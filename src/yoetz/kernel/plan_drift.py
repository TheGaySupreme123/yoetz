"""Advisory drift signals for plans that no longer describe the work.

The signals in this module are intentionally derived from recorded frontiers and explicit
relations.  They never inspect plan prose, action descriptions, or the user's request looking
for similar words.  A signal is therefore a bounded diagnostic: it says that the ledger lacks a
relation needed to read the plan as current, not that the work is incomplete.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from yoetz.domain.events import (
    AcceptedEvent,
    ActionKind,
    ActionRecordedPayload,
    LedgerRecord,
    ObligationStatus,
    PlanPublishedPayload,
    PlanRevisedPayload,
)
from yoetz.domain.task_statement import current_task_statement
from yoetz.domain.values import EvidenceId, ObligationId, ResultId
from yoetz.kernel.plan_scope import current_plan_scope
from yoetz.kernel.projections import PlanProjectionRecord, ProjectionState

__all__ = [
    "INSTRUCTION_REQUIREMENT_UNMAPPED_GAP",
    "OBLIGATION_EVIDENCE_STALE_AFTER_SCOPE_EDIT_GAP",
    "PLAN_DRIFT_GAPS",
    "PLAN_UNREFINED_BEFORE_FIRST_EDIT_GAP",
    "PlanDriftSignals",
    "plan_drift_signals",
]


# These are advisory coverage annotations.  Callers must keep them visible in status/check and
# receipt text while excluding them from completion verdicts and closure blockers.
PLAN_UNREFINED_BEFORE_FIRST_EDIT_GAP: Final = "plan_unrefined_before_first_edit"
OBLIGATION_EVIDENCE_STALE_AFTER_SCOPE_EDIT_GAP: Final = "obligation_evidence_stale_after_scope_edit"
INSTRUCTION_REQUIREMENT_UNMAPPED_GAP: Final = "instruction_requirement_unmapped"
PLAN_DRIFT_GAPS: Final = frozenset(
    {
        PLAN_UNREFINED_BEFORE_FIRST_EDIT_GAP,
        OBLIGATION_EVIDENCE_STALE_AFTER_SCOPE_EDIT_GAP,
        INSTRUCTION_REQUIREMENT_UNMAPPED_GAP,
    }
)


@dataclass(frozen=True, slots=True)
class PlanDriftSignals:
    """Closed, deterministic plan drift facts for one accepted prefix."""

    codes: tuple[str, ...] = ()
    stale_obligation_ids: tuple[ObligationId, ...] = ()

    def __post_init__(self) -> None:
        if type(self.codes) is not tuple or self.codes != tuple(
            sorted(set(self.codes), key=str.encode)
        ):
            raise ValueError("plan_drift_signals_invalid")
        if not set(self.codes) <= PLAN_DRIFT_GAPS:
            raise ValueError("plan_drift_signals_invalid")
        if type(self.stale_obligation_ids) is not tuple or self.stale_obligation_ids != tuple(
            sorted(set(self.stale_obligation_ids), key=str.encode)
        ):
            raise ValueError("plan_drift_signals_invalid")
        if bool(self.stale_obligation_ids) != (
            OBLIGATION_EVIDENCE_STALE_AFTER_SCOPE_EDIT_GAP in self.codes
        ):
            raise ValueError("plan_drift_signals_invalid")

    @property
    def present(self) -> bool:
        return bool(self.codes)


def _ordered_records(records: Iterable[LedgerRecord]) -> tuple[LedgerRecord, ...]:
    return tuple(sorted(records, key=lambda row: row.ledger.ingestion_sequence))


def _first_material_edit(records: tuple[LedgerRecord, ...]) -> int | None:
    frontiers = [
        record.ledger.ingestion_sequence
        for record in records
        if type(record) is AcceptedEvent
        and type(record.payload) is ActionRecordedPayload
        and record.payload.action_kind is ActionKind.EDIT
    ]
    return min(frontiers, default=None)


def _plan_unrefined_before_edit(
    plans: Mapping[int, PlanProjectionRecord], first_edit_frontier: int | None
) -> bool:
    if first_edit_frontier is None:
        return False
    ordered = tuple(
        sorted(
            (
                record
                for record in plans.values()
                if type(getattr(record, "payload", None))
                in {PlanPublishedPayload, PlanRevisedPayload}
            ),
            key=lambda record: (
                record.source_frontier,
                str(record.source_event_id).encode("ascii"),
            ),
        )
    )
    if not ordered:
        return False
    before_edit = tuple(
        record for record in ordered if record.source_frontier <= first_edit_frontier
    )
    if not before_edit:
        # A plan published only after the first edit cannot establish this particular timing gap.
        return False
    initial = before_edit[0]
    # A later valid revision repairs the stale-plan condition even when the agent refined only
    # after its first edit.  A later full ``plan_published`` restatement is not the required
    # refinement: it has no carried/superseded/waived obligation relation, so it must not clear
    # the signal merely by replacing the effective scope.
    return not any(
        type(getattr(record, "payload", None)) is PlanRevisedPayload
        and record.source_frontier > initial.source_frontier
        for record in ordered[1:]
    )


def _reference_frontier(
    reference: EvidenceId | ResultId,
    projection: ProjectionState,
) -> int | None:
    if reference.startswith("evd_"):
        row = projection.evidence.get(EvidenceId(str(reference)))
    elif reference.startswith("res_"):
        row = projection.results.get(ResultId(str(reference)))
    else:  # The payload validator should make this unreachable.
        return None
    return None if row is None or row.payload is None else row.source_frontier


def _stale_obligations(
    projection: ProjectionState,
    records: tuple[LedgerRecord, ...],
) -> tuple[ObligationId, ...]:
    scope = current_plan_scope(projection.plans, projection.coverage_gaps)
    if not scope.readable or scope.effective_obligation_refs is None:
        return ()
    edits_by_obligation: dict[ObligationId, int] = {}
    unscoped_edit_frontier: int | None = None
    for record in records:
        if (
            type(record) is not AcceptedEvent
            or type(record.payload) is not ActionRecordedPayload
            or record.payload.action_kind is not ActionKind.EDIT
        ):
            continue
        if not record.payload.obligation_refs:
            unscoped_edit_frontier = max(
                unscoped_edit_frontier or 0,
                record.ledger.ingestion_sequence,
            )
            continue
        for obligation in record.payload.obligation_refs:
            edits_by_obligation[obligation] = max(
                edits_by_obligation.get(obligation, 0), record.ledger.ingestion_sequence
            )

    stale: list[ObligationId] = []
    for obligation in scope.effective_obligation_refs:
        row = projection.obligations.get(obligation)
        payload = None if row is None else row.payload
        if payload is None or payload.status is not ObligationStatus.RESOLVED:
            continue
        if not payload.resolution_evidence_refs:
            continue
        scoped_edit_frontier = edits_by_obligation.get(obligation)
        if scoped_edit_frontier is None:
            edit_frontier = unscoped_edit_frontier
        elif unscoped_edit_frontier is None:
            edit_frontier = scoped_edit_frontier
        else:
            # A global edit applies to every obligation even when an earlier scoped edit also
            # exists.  Use the latest frontier so a later global change cannot be hidden by the
            # first obligation-specific edit.
            edit_frontier = max(scoped_edit_frontier, unscoped_edit_frontier)
        if edit_frontier is None:
            continue
        evidence_frontiers = tuple(
            frontier
            for reference in payload.resolution_evidence_refs
            if (frontier := _reference_frontier(reference, projection)) is not None
        )
        # Unknown evidence remains a separate ledger coverage problem.  This signal only claims
        # staleness when every retained resolution reference is readable and predates the edit.
        if (
            len(evidence_frontiers) == len(payload.resolution_evidence_refs)
            and evidence_frontiers
            and max(evidence_frontiers) < edit_frontier
        ):
            stale.append(obligation)
    return tuple(sorted(set(stale), key=str.encode))


def _instruction_unmapped(projection: ProjectionState, records: tuple[LedgerRecord, ...]) -> bool:
    statement = current_task_statement(records)
    scope = current_plan_scope(projection.plans, projection.coverage_gaps)
    if statement is None or not scope.readable or scope.effective_obligation_refs is None:
        return False
    if not scope.effective_obligation_refs:
        # An explicit empty-scope declaration is a plan decision, not evidence that a requirement
        # was accidentally omitted from the plan.
        return False
    return not any(
        row.payload is not None and statement.source_event_id in row.payload.source_refs
        for obligation in scope.effective_obligation_refs
        if (row := projection.obligations.get(obligation)) is not None
    )


def plan_drift_signals(
    projection: ProjectionState,
    records: Iterable[LedgerRecord],
) -> PlanDriftSignals:
    """Derive advisory plan signals from exact ledger relations.

    A caller must pass the same accepted prefix that produced ``projection``.  The helper is
    deliberately total for a readable projection and ignores unknown/redacted payloads rather
    than treating them as evidence of drift.
    """

    if type(projection) is not ProjectionState:
        raise TypeError("plan_drift_projection_invalid")
    ordered = _ordered_records(records)
    first_edit = _first_material_edit(ordered)
    codes: set[str] = set()
    plan_scope = current_plan_scope(projection.plans, projection.coverage_gaps)
    if plan_scope.readable and _plan_unrefined_before_edit(projection.plans, first_edit):
        codes.add(PLAN_UNREFINED_BEFORE_FIRST_EDIT_GAP)
    stale = _stale_obligations(projection, ordered)
    if stale:
        codes.add(OBLIGATION_EVIDENCE_STALE_AFTER_SCOPE_EDIT_GAP)
    if _instruction_unmapped(projection, ordered):
        codes.add(INSTRUCTION_REQUIREMENT_UNMAPPED_GAP)
    return PlanDriftSignals(tuple(sorted(codes, key=str.encode)), stale)
