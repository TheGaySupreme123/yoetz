"""What an ``insufficient_packet`` review named as missing, fenced and classified (issue #907).

The reviewer may name only refs its packet carried. Yoetz, not the reviewer, decides whether the
agent can supply each item: a kind the effective review selection or channel can never carry, or a
target the owner redacted, is ``structurally_unavailable_on_this_host``; everything else is
``agent_suppliable``. A later review sees the earlier request beside what was recorded since, and
an item re-requested after it was supplied is dropped unless the reviewer cites that new material.
"Supplied" is judged per named target: only material the ledger's own refs tie to that target
counts, so material for another path, run or claim never answers it.

Pure: reads only the frozen projection and the frozen review selection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from yoetz.domain.events import (
    ActionRecordedPayload,
    ClaimRecordedPayloadV1_1,
    EvidenceDigestProvenance,
    EvidenceRecordedPayload,
    MissingForAssessmentItem,
    PlanPublishedPayload,
    PlanRevisedPayload,
    ResultRecordedPayload,
)
from yoetz.domain.findings import Finding
from yoetz.domain.privacy import ReviewSelectionPolicy
from yoetz.domain.receipts import (
    SEMANTIC_MISSING_AGENT_SUPPLIABLE_GAP,
    SEMANTIC_MISSING_ALREADY_SUPPLIED_GAP,
    SEMANTIC_MISSING_ITEMS_REJECTED_GAP,
    SEMANTIC_MISSING_UNAVAILABLE_GAP,
)
from yoetz.domain.values import EventId
from yoetz.domain.values import action_id as validate_action_id
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.kernel.projections import PendingMissingForAssessment, ProjectionState
from yoetz.ports.semantic import SemanticJudgment
from yoetz.protocol.models import MISSING_FOR_ASSESSMENT_KINDS, DataCategory

__all__ = [
    "MissingItemsReview",
    "review_missing_for_assessment",
    "supplied_since",
    "unsuppliable_missing_kinds",
]

AGENT_SUPPLIABLE: Final = "agent_suppliable"
STRUCTURALLY_UNAVAILABLE: Final = "structurally_unavailable_on_this_host"
# How many refs recorded since the request the next packet names per item, newest first.
_MAX_SUPPLIED_REFS: Final = 4
# Which recorded families can answer each missing kind. ``output_results`` are results that
# carry output (linked evidence or a summary): a bare outcome row names a run but not what it
# printed, so it can never answer a request for verification output.
_ANSWERING_FAMILIES: Final[Mapping[str, tuple[str, ...]]] = {
    "command_identity": ("actions", "results", "evidence"),
    "current_diff_for_path": ("evidence",),
    "other": ("evidence", "results"),
    "plan_or_claim_text": ("plans", "claims"),
    "prior_finding_context": ("responses",),
    "task_statement": ("plans",),
    "verification_output": ("evidence", "output_results"),
}
_FAMILY_NAMES: Final = (
    "actions",
    "results",
    "output_results",
    "evidence",
    "claims",
    "plans",
    "responses",
)


@dataclass(frozen=True, slots=True)
class MissingItemsReview:
    """The fenced, classified items a check records, and the coverage gaps they imply."""

    items: tuple[MissingForAssessmentItem, ...]
    gaps: frozenset[str]


def unsuppliable_missing_kinds(
    selection: ReviewSelectionPolicy, withheld_categories: Iterable[str]
) -> tuple[str, ...]:
    """Kinds the effective selection and channel can never carry, whatever the agent records.

    Publishing more material does not help when the approved review selection drops its section
    or excerpt kind, or the inference channel withholds its category; that is a limitation of this
    host's configuration, not work the agent can do.
    """

    withheld = frozenset(withheld_categories)
    sections = frozenset(selection.sections)
    kinds = frozenset(selection.excerpt_kinds)
    excerpts = (
        "targeted_excerpts" in sections
        and selection.max_excerpts > 0
        and DataCategory.EVIDENCE_EXCERPT.value not in withheld
    )
    goal = "goal" in sections and DataCategory.TASK_DESCRIPTION.value not in withheld
    claims = "claims" in sections and DataCategory.CLAIM_TEXT.value not in withheld
    carried = {
        "command_identity": (
            "targeted_excerpts" in sections
            and selection.max_excerpts > 0
            and selection.include_exact_command_text
            and "command" in kinds
            and DataCategory.COMMAND_METADATA.value not in withheld
        ),
        "current_diff_for_path": excerpts and bool({"diff", "evidence"} & kinds),
        "other": excerpts,
        "plan_or_claim_text": goal or claims,
        "prior_finding_context": (
            "timeline" in sections and DataCategory.FINDING_SUMMARY.value not in withheld
        ),
        "task_statement": goal,
        "verification_output": excerpts
        and bool({"command", "evidence", "failure", "test"} & kinds),
    }
    if frozenset(carried) != MISSING_FOR_ASSESSMENT_KINDS:
        raise RuntimeError("missing_for_assessment_kinds_incomplete")
    return tuple(sorted((kind for kind, ok in carried.items() if not ok), key=str.encode))


def supplied_since(
    projection: ProjectionState,
    pending: PendingMissingForAssessment,
    allowed: frozenset[str],
    observation_event_ids: frozenset[str] = frozenset(),
) -> tuple[tuple[str, ...], ...]:
    """For each pending item, the case refs of answering material recorded after the request.

    A targeted item counts only material bound to one of its targets through recorded refs (see
    ``_RecordedLinks``); an item with no target counts any answering material of its kind. Yoetz
    cannot bind new material to reviewer prose, so the next reviewer reads these refs and
    decides whether they answer the request.
    """

    answered: list[tuple[str, ...]] = []
    for per_target in _answers_by_target(projection, pending, allowed, observation_event_ids):
        newest = sorted(
            {entry for entries in per_target.values() for entry in entries},
            key=lambda entry: (-entry[0], entry[1].encode("ascii")),
        )
        answered.append(
            tuple(sorted((ref for _order, ref in newest[:_MAX_SUPPLIED_REFS]), key=str.encode))
        )
    return tuple(answered)


def _answers_by_target(
    projection: ProjectionState,
    pending: PendingMissingForAssessment,
    allowed: frozenset[str],
    observation_event_ids: frozenset[str],
) -> tuple[dict[str | None, tuple[tuple[int, str], ...]], ...]:
    """Per pending item and target, the answering material recorded since, as ``(order, ref)``.

    The key ``None`` holds an untargeted item's answers, matched by record family alone.
    """

    after = pending.source_frontier
    recorded: dict[str, list[tuple[int, str]]] = {family: [] for family in _FAMILY_NAMES}
    for family, rows in (
        ("actions", projection.actions),
        ("results", projection.results),
        ("evidence", projection.evidence),
        ("claims", projection.claims),
    ):
        for ref, row in rows.items():
            if row.source_frontier > after and row.payload is not None and not row.redacted:
                if str(row.source_event_id) in observation_event_ids or _hook_observed(row.payload):
                    # Hook capture records every tool call; it is never the agent answering a
                    # named request, so it must not turn a still-missing item into "supplied".
                    continue
                recorded[family].append((row.source_frontier, str(ref)))
                if type(row.payload) is ResultRecordedPayload and _carries_output(row.payload):
                    recorded["output_results"].append((row.source_frontier, str(ref)))
    for row in projection.plans.values():
        if row.source_frontier > after and row.payload is not None and not row.redacted:
            recorded["plans"].append((row.source_frontier, str(row.source_event_id)))
    for row in projection.responses.values():
        if row.source_frontier > after and row.payload is not None and not row.redacted:
            recorded["responses"].append((row.source_frontier, str(row.source_event_id)))
    graph = _RecordedLinks.of(projection, after, observation_event_ids)
    answers: list[dict[str | None, tuple[tuple[int, str], ...]]] = []
    for item in pending.items:
        candidates = tuple(
            entry
            for family in _ANSWERING_FAMILIES[item.kind]
            for entry in recorded[family]
            if entry[1] in allowed
        )
        if not item.target_refs:
            answers.append({None: candidates})
            continue
        answers.append(
            {
                target: tuple(entry for entry in candidates if entry[1] in bound)
                for target in item.target_refs
                for bound in (graph.bound_since(target),)
            }
        )
    return tuple(answers)


@dataclass(frozen=True, slots=True)
class _RecordedLinks:
    """The recorded refs that tie agent material published after a request to its targets.

    Nodes are the agent-published, unredacted rows recorded after the request; each names the
    refs its payload cites (a result its action and evidence, a claim its support, scope and the
    claims it replaces or disputes, a response its finding and evidence, an action its
    obligations). A target stands for itself and for what identifies the same subject: the
    action behind a result or cited evidence, every recorded run of that action's exact command
    (never a hook placeholder),
    evidence at the same agent-recorded ``reference``, a claim's obligation scope, and a plan's
    later versions. Material is bound to a target when a chain of those citations among newer
    agent rows reaches it. A plan names every obligation, so it can be bound but never binds
    anything else; older rows outside the target's identity never bridge two subjects.
    """

    names: Mapping[str, frozenset[str]]
    terminal: frozenset[str]
    identity: Mapping[str, frozenset[str]]

    @classmethod
    def of(
        cls,
        projection: ProjectionState,
        after: int,
        observation_event_ids: frozenset[str],
    ) -> _RecordedLinks:
        names: dict[str, frozenset[str]] = {}
        terminal: set[str] = set()

        def newer(row: _Row) -> bool:
            return (
                row.source_frontier > after
                and row.payload is not None
                and not row.redacted
                and str(row.source_event_id) not in observation_event_ids
                and not _hook_observed(row.payload)
            )

        for ref, row in projection.actions.items():
            if newer(row) and row.payload is not None:
                names[str(ref)] = frozenset(str(item) for item in row.payload.obligation_refs)
        for ref, row in projection.results.items():
            if newer(row) and row.payload is not None:
                names[str(ref)] = frozenset(
                    (str(row.payload.action_id), *(str(item) for item in row.payload.evidence_refs))
                )
        for ref, row in projection.evidence.items():
            if newer(row):
                names[str(ref)] = frozenset()
        for ref, row in projection.obligations.items():
            if newer(row):
                names[str(ref)] = frozenset()
        for ref, row in projection.claims.items():
            if newer(row) and row.payload is not None:
                claim = row.payload
                names[str(ref)] = frozenset(
                    str(item)
                    for item in (
                        *claim.supporting_refs,
                        *claim.obligation_refs,
                        *claim.disputes_refs,
                        *(
                            (*claim.limitation_refs, *claim.supersedes_claim_refs)
                            if type(claim) is ClaimRecordedPayloadV1_1
                            else ()
                        ),
                    )
                )
        for row in projection.responses.values():
            if newer(row) and row.payload is not None:
                names[str(row.source_event_id)] = frozenset(
                    (
                        str(row.payload.finding_id),
                        *(str(item) for item in row.payload.evidence_refs),
                    )
                )
        plan_events = {
            version: str(row.source_event_id) for version, row in projection.plans.items()
        }
        for row in projection.plans.values():
            if newer(row) and row.payload is not None:
                plan = row.payload
                cited: set[str] = set()
                if type(plan) is PlanPublishedPayload:
                    cited.update(str(item) for item in plan.obligation_refs)
                elif type(plan) is PlanRevisedPayload:
                    prior = plan_events.get(plan.supersedes_plan_version)
                    if prior is not None:
                        cited.add(prior)
                    for change in plan.obligation_changes:
                        cited.add(str(change.obligation_id))
                        cited.update(str(item) for item in change.replacement_obligation_ids)
                names[str(row.source_event_id)] = frozenset(cited)
                terminal.add(str(row.source_event_id))
        return cls(names, frozenset(terminal), _target_identities(projection))

    def bound_since(self, target: str) -> frozenset[str]:
        """Newer agent rows a chain of recorded citations ties to ``target``'s subject."""

        reached: set[str] = set(self.identity.get(target, frozenset({target})))
        reached.add(target)
        changed = True
        while changed:
            changed = False
            for ref, cited in self.names.items():
                if ref not in reached and cited & reached:
                    reached.add(ref)
                    changed = True
                if ref in reached and ref not in self.terminal:
                    onward = {item for item in cited if item in self.names} - reached
                    if onward:
                        reached.update(onward)
                        changed = True
        return frozenset(reached)


class _Row(Protocol):
    @property
    def payload(self) -> object | None: ...
    @property
    def redacted(self) -> bool: ...
    @property
    def source_event_id(self) -> EventId: ...
    @property
    def source_frontier(self) -> int: ...


def _target_identities(projection: ProjectionState) -> dict[str, frozenset[str]]:
    """What else identifies the subject a target names (see ``_RecordedLinks``)."""

    by_command: dict[str, set[str]] = {}
    for ref, row in projection.actions.items():
        if (command := _command_identity(row.payload)) is not None:
            by_command.setdefault(command, set()).add(str(ref))
    by_reference: dict[str, set[str]] = {}
    for ref, row in projection.evidence.items():
        if row.payload is not None and row.payload.reference:
            by_reference.setdefault(row.payload.reference, set()).add(str(ref))

    def runs_of(action: str) -> set[str]:
        row = projection.actions.get(validate_action_id(action))
        command = None if row is None else _command_identity(row.payload)
        return {action} if command is None else {action, *by_command[command]}

    identity: dict[str, frozenset[str]] = {}
    for ref, row in projection.actions.items():
        identity[str(ref)] = frozenset(runs_of(str(ref)))
    cited_by: dict[str, set[str]] = {}
    for ref, row in projection.results.items():
        if row.payload is not None:
            runs = runs_of(str(row.payload.action_id))
            identity[str(ref)] = frozenset({str(ref), *runs})
            for evidence_ref in row.payload.evidence_refs:
                cited_by.setdefault(str(evidence_ref), set()).update(runs)
    for ref, row in projection.evidence.items():
        same: set[str] = (
            set()
            if row.payload is None or not row.payload.reference
            else by_reference[row.payload.reference]
        )
        identity[str(ref)] = frozenset({str(ref), *same, *cited_by.get(str(ref), ())})
    for ref, row in projection.claims.items():
        if row.payload is not None:
            identity[str(ref)] = frozenset(
                {str(ref), *(str(item) for item in row.payload.obligation_refs)}
            )
    return identity


def _command_identity(payload: ActionRecordedPayload | None) -> str | None:
    """The exact command text that makes two runs the same, or ``None`` when none is recorded.

    Hook placeholders (``omitted:...``) stand in for text Yoetz did not keep, so two of them are
    never treated as the same command.
    """

    if payload is None or payload.command is None or payload.command.startswith("omitted:"):
        return None
    return payload.command


def _carries_output(payload: ResultRecordedPayload) -> bool:
    return bool(payload.evidence_refs) or bool(payload.summary and payload.summary.strip())


def _hook_observed(payload: object) -> bool:
    """Hook-captured bytes carry service-stamped observation provenance on their evidence row."""

    return (
        type(payload) is EvidenceRecordedPayload
        and payload.digest_binding is not None
        and payload.digest_binding.provenance is EvidenceDigestProvenance.OBSERVATION_CAPTURED
    )


def _redacted_refs(projection: ProjectionState) -> frozenset[str]:
    redacted: set[str] = set()
    for rows in (
        projection.obligations,
        projection.actions,
        projection.results,
        projection.evidence,
        projection.claims,
        projection.findings,
    ):
        for ref, row in rows.items():
            if row.redacted:
                redacted.update((str(ref), str(row.source_event_id)))
    return frozenset(redacted)


def review_missing_for_assessment(
    case: DeterministicCase,
    deterministic: tuple[Finding, ...],
    judgment: SemanticJudgment,
    *,
    unsuppliable_kinds: frozenset[str],
    citable_refs: frozenset[str] | None = None,
) -> MissingItemsReview:
    """Fence what the reviewer named to the packet it was shown and classify who can supply it.

    A target outside the packet's ``citable_refs`` (the frozen case when the composing evaluator
    did not report the packet) is dropped (``semantic_missing_items_rejected``), as #905 trims a
    ruling's cited refs; an item whose every target was outside is dropped whole, because the
    reviewer named nothing the packet held.
    An ``insufficient_packet`` that named no item at all discloses the same gap.
    An item the prior review already requested and the agent answered since, target by target,
    is dropped unless the reviewer cites that newer material (``semantic_missing_already_supplied``),
    so the same request cannot loop; material for another target never answers it. Every kept item is recorded with Yoetz's own availability class.
    """

    if judgment.conclusion != "insufficient_packet":
        return MissingItemsReview((), frozenset())
    if not judgment.missing_for_assessment:
        # A reply in the 1.0.0 shape (a local model or prompt-only host) named nothing: the agent
        # cannot tell what to supply, so the check says so instead of reading as a named request.
        return MissingItemsReview((), frozenset({SEMANTIC_MISSING_ITEMS_REJECTED_GAP}))
    allowed = frozenset(str(ref) for ref in case.allowed_ids) | frozenset(
        str(item.finding_id) for item in deterministic
    )
    projection = case.projection
    pending = projection.pending_missing_for_assessment
    observed = frozenset(str(item) for item in case.observation_event_ids)
    answered = () if pending is None else _answers_by_target(projection, pending, allowed, observed)
    redacted = _redacted_refs(projection)
    shown = allowed if citable_refs is None else allowed & citable_refs
    gaps: set[str] = set()
    kept: dict[tuple[str, tuple[str, ...]], str] = {}
    for entry in judgment.missing_for_assessment:
        refs = tuple(ref for ref in entry.target_refs if ref in shown)
        if len(refs) != len(entry.target_refs):
            gaps.add(SEMANTIC_MISSING_ITEMS_REJECTED_GAP)
            if not refs:
                continue
        if pending is not None and any(
            prior.kind == entry.kind and _answered(prior, per_target, refs)
            for prior, per_target in zip(pending.items, answered, strict=True)
        ):
            gaps.add(SEMANTIC_MISSING_ALREADY_SUPPLIED_GAP)
            continue
        availability = (
            STRUCTURALLY_UNAVAILABLE
            if entry.kind in unsuppliable_kinds or set(refs) & redacted
            else AGENT_SUPPLIABLE
        )
        key = (entry.kind, refs)
        if kept.get(key) != STRUCTURALLY_UNAVAILABLE:
            kept[key] = availability
    items = tuple(
        MissingForAssessmentItem(kind=kind, target_refs=refs, availability=availability)
        for (kind, refs), availability in sorted(
            kept.items(),
            key=lambda pair: (
                pair[0][0].encode("ascii"),
                tuple(ref.encode("ascii") for ref in pair[0][1]),
            ),
        )
    )
    for item in items:
        gaps.add(
            SEMANTIC_MISSING_AGENT_SUPPLIABLE_GAP
            if item.availability == AGENT_SUPPLIABLE
            else SEMANTIC_MISSING_UNAVAILABLE_GAP
        )
    return MissingItemsReview(items, frozenset(gaps))


def _answered(
    prior: MissingForAssessmentItem,
    per_target: Mapping[str | None, tuple[tuple[int, str], ...]],
    refs: tuple[str, ...],
) -> bool:
    """Whether the prior request named every one of ``refs`` and each was answered since.

    An untargeted repeat of an untargeted request is answered by any material of its kind. A
    target the prior request did not name is a new request; a repeat that cites the newer
    material says it is still insufficient, so neither is dropped.
    """

    if not refs:
        return not prior.target_refs and bool(per_target.get(None))
    supplied = {ref for entries in per_target.values() for _order, ref in entries}
    return (
        set(refs) <= set(prior.target_refs)
        and all(per_target.get(ref) for ref in refs)
        and not supplied & set(refs)
    )
