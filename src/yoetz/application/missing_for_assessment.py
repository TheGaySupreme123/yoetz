"""What an ``insufficient_packet`` review named as missing, fenced and classified (issue #907).

The reviewer may name only refs its packet carried. Yoetz, not the reviewer, decides whether the
agent can supply each item: a kind the effective review selection or channel can never carry, or a
target the owner redacted, is ``structurally_unavailable_on_this_host``; everything else is
``agent_suppliable``. A later review sees the earlier request beside what was recorded since, and
an item re-requested after it was supplied is dropped unless the reviewer cites that new material.

Pure: reads only the frozen projection and the frozen review selection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from yoetz.domain.events import (
    EvidenceDigestProvenance,
    EvidenceRecordedPayload,
    MissingForAssessmentItem,
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

    This is recording order only: Yoetz cannot bind new material to reviewer prose, so the next
    reviewer reads these refs and decides whether they answer the request.
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
    answered: list[tuple[str, ...]] = []
    for item in pending.items:
        candidates = sorted(
            (
                entry
                for family in _ANSWERING_FAMILIES[item.kind]
                for entry in recorded[family]
                if entry[1] in allowed
            ),
            key=lambda entry: (-entry[0], entry[1].encode("ascii")),
        )
        answered.append(
            tuple(sorted((ref for _order, ref in candidates[:_MAX_SUPPLIED_REFS]), key=str.encode))
        )
    return tuple(answered)


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
    An item the prior review already requested and the agent answered since is dropped unless
    the reviewer cites that newer material (``semantic_missing_already_supplied``), so the same
    request cannot loop. Every kept item is recorded with Yoetz's own availability class.
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
    answered = () if pending is None else supplied_since(projection, pending, allowed, observed)
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
            prior.kind == entry.kind
            and (bool(set(prior.target_refs) & set(refs)) or not (prior.target_refs or refs))
            and supplied
            and not set(supplied) & set(refs)
            for prior, supplied in zip(pending.items, answered, strict=True)
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
