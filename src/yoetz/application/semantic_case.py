"""Pure frozen-authority builder for privacy-selected semantic review cases.

Constructs ``SemanticCase`` / ``ReviewPacket`` from the already-frozen check case,
pinned deterministic findings/bases, and the active ``ReviewSelectionPolicy``.

This module is deliberately capability-free: no Git, filesystem, network, transcript,
environment, database, or provider access. Missing material becomes an omission.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Final, Literal, cast

from yoetz.application.check import (
    CheckScope,
    case_coverage,
    run_deterministic_policies,
)
from yoetz.domain.events import (
    ActionKind,
    ClaimRecordedPayload,
    DecisionRecordedPayload,
    EvidenceKind,
    EvidenceRecordedPayload,
    ObligationPublishedPayload,
    ResultOutcome,
    encode_payload,
)
from yoetz.domain.findings import Finding, FindingKind
from yoetz.domain.privacy import (
    MAX_EGRESS_ENVELOPE_BYTES,
    REVIEW_PACKET_ITEM_ID,
    AuthorizationScope,
    CandidateContext,
    CandidateContextItem,
    EgressChannel,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.receipts import SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP
from yoetz.domain.values import SubjectStateRelation
from yoetz.kernel.deterministic_checks import (
    DeterministicAssessment,
    DeterministicCase,
    FrozenHistoryEvent,
)
from yoetz.ports.semantic import (
    ChangeObservation,
    ExcerptDigestProvenance,
    ReviewAssessment,
    ReviewAssessmentSkipped,
    ReviewOmission,
    ReviewPacket,
    SemanticCase,
    SemanticCaseItem,
    TargetedExcerptRef,
    project_review_assessment,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.coverage import LedgerFreshness, coverage_to_json
from yoetz.protocol.models import (
    MAX_REVIEW_TEXT_BYTES,
    MAX_SEMANTIC_ITEM_BYTES,
    DataCategory,
)

__all__ = [
    "OVER_CASE_ITEM_LIMIT_REASON",
    "REVIEW_PACKET_ITEM_ID",
    "SEMANTIC_REVIEW_PURPOSE",
    "assemble_filtered_review_packet",
    "build_semantic_case",
    "review_selection_digest",
    "semantic_case_to_candidate_context",
    "semantic_case_to_prepared_payload",
]

SEMANTIC_REVIEW_PURPOSE: Final = "semantic-review"
# Marker reason for content the case admitted and then could not carry whole. Distinct from the
# `not_selected` omission vocabulary, which means the selection policy declined to carry it.
OVER_CASE_ITEM_LIMIT_REASON: Final = "over_case_item_limit"
_PACKET_SCHEMA: Final = "yoetz.review-packet-case/1"
_PACKET_ID_LIST_KEYS: Final = (
    "goal_item_ids",
    "obligation_item_ids",
    "claim_item_ids",
    "decision_item_ids",
    "timeline_item_ids",
)
_CANONICAL_PACKS: Final = ("research-evidence/0.1.0", "work-integrity/0.1.0")
_QUESTION_SET: Final = (
    "Does the supplied packet contain a material discrepancy against the goal and obligations?",
    "If so, which case-bound refs support the discrepancy?",
    "What is the smallest next step the main agent should take?",
)

type _Section = Literal[
    "goal",
    "obligation",
    "claim",
    "decision",
    "timeline",
    "deterministic_summary",
    "deterministic_detail",
    "excerpt",
]
type _SourceKind = Literal[
    "task",
    "obligation",
    "claim",
    "decision",
    "action",
    "result",
    "evidence",
    "finding",
    "test",
    "failure",
    "diff",
    "command",
    "repository",
]
type _ExcerptKind = Literal["evidence", "test", "failure", "diff", "command", "repository"]
type _OmissionReason = Literal[
    "not_recorded", "not_selected", "withheld_by_policy", "redacted_never_send"
]

_EVIDENCE_EXCERPT_KIND: Final[Mapping[EvidenceKind, _ExcerptKind]] = {
    EvidenceKind.ARTIFACT: "evidence",
    EvidenceKind.COMMAND_OUTPUT: "command",
    EvidenceKind.TEST_RESULT: "test",
    EvidenceKind.RESEARCH_SOURCE: "repository",
    EvidenceKind.IMPORT_REPORT: "evidence",
    EvidenceKind.OTHER: "evidence",
}
_HISTORY_KIND: Final[Mapping[str, tuple[_SourceKind, DataCategory]]] = {
    "action_recorded": ("action", DataCategory.COMMAND_METADATA),
    "check_recorded": ("finding", DataCategory.BOUNDED_STRUCTURAL_METADATA),
    "claim_recorded": ("claim", DataCategory.CLAIM_TEXT),
    "decision_recorded": ("decision", DataCategory.DECISION_EXCERPT),
    "evidence_recorded": ("evidence", DataCategory.EVIDENCE_EXCERPT),
    "finding_recorded": ("finding", DataCategory.FINDING_SUMMARY),
    "obligation_published": ("obligation", DataCategory.OBLIGATION_TEXT),
    "plan_published": ("task", DataCategory.TASK_DESCRIPTION),
    "plan_revised": ("task", DataCategory.TASK_DESCRIPTION),
    "response_recorded": ("finding", DataCategory.FINDING_SUMMARY),
    "result_recorded": ("result", DataCategory.COMMAND_METADATA),
}


def review_selection_digest(selection: ReviewSelectionPolicy) -> str:
    """Digest the closed review-selection value for case/provenance binding."""

    if type(selection) is not ReviewSelectionPolicy:
        raise TypeError("review_selection_invalid")
    return canonical_digest(
        cast(
            JsonValue,
            {
                "excerpt_kinds": list(selection.excerpt_kinds),
                "include_exact_command_text": selection.include_exact_command_text,
                "include_finding_prose": selection.include_finding_prose,
                "max_assessments": selection.max_assessments,
                "max_change_observations": selection.max_change_observations,
                "max_excerpt_bytes": selection.max_excerpt_bytes,
                "max_excerpts": selection.max_excerpts,
                "max_omissions": selection.max_omissions,
                "max_timeline_items": selection.max_timeline_items,
                "max_total_excerpt_bytes": selection.max_total_excerpt_bytes,
                "relevance": selection.relevance,
                "schema": "yoetz.review-selection/1",
                "sections": list(selection.sections),
            },
        )
    )


def _utf8(text: str, *, maximum: int = MAX_SEMANTIC_ITEM_BYTES) -> bytes:
    encoded = text.encode("utf-8")
    if not 1 <= len(encoded) <= maximum:
        raise ValueError("semantic_case_content_invalid")
    return encoded


def _content_item(
    *,
    item_id: str,
    section: _Section,
    category: DataCategory,
    source_kind: _SourceKind,
    source_ref: str,
    linked_subject_refs: tuple[str, ...],
    occurred_order: int,
    text: str,
    over_limit: set[str] | None = None,
) -> SemanticCaseItem:
    # Bound by UTF-8 bytes, not characters — multi-byte prose must not raise.
    limit = min(MAX_REVIEW_TEXT_BYTES, MAX_SEMANTIC_ITEM_BYTES)
    raw = text.encode("utf-8")
    if len(raw) > limit:
        # Publish-side prose accepts up to MAX_PROSE_CHARS, which is twice what one case item can
        # carry. Silent truncation here is what made a 5 KB evidence description publish cleanly
        # and then reach the reviewer as a shortened fragment with nothing saying so (issue #177).
        raw = raw[:limit].decode("utf-8", errors="ignore").encode("utf-8")
        if over_limit is not None:
            over_limit.add(item_id)
    if not raw:
        raise ValueError("semantic_case_content_invalid")
    content = _utf8(raw.decode("utf-8"), maximum=limit)
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return SemanticCaseItem(
        item_id=item_id,
        section=section,
        category=category,
        source_kind=source_kind,
        source_ref=source_ref,
        linked_subject_refs=linked_subject_refs,
        occurred_order=occurred_order,
        content=content,
        content_bytes=len(content),
        content_digest=digest,
    )


def _structural_json(value: Mapping[str, JsonValue]) -> str:
    # canonical_encode emits UTF-8 and does not escape non-ASCII, so these must be decoded as
    # UTF-8. Decoding as ASCII meant a single em dash, curly quote or accented character anywhere
    # in the ledger raised UnicodeDecodeError while building the case — surfacing as
    # coordinator_failure with no semantic review at all. Agents write such characters constantly.
    return canonical_encode(cast(JsonValue, dict(value))).decode("utf-8")


def _bounded_json(value: Mapping[str, JsonValue]) -> tuple[str, bool]:
    encoded = canonical_encode(cast(JsonValue, dict(value)))
    if len(encoded) <= MAX_REVIEW_TEXT_BYTES:
        return encoded.decode("utf-8"), False
    # `not_selected` read as a selection-policy choice, indistinguishable from a section the
    # profile declined to carry. The payload was in fact admitted and then dropped for size, and
    # the reviewer needs to know which of the two happened (issue #177).
    marker = {
        "content_digest": canonical_digest(cast(JsonValue, dict(value))),
        "original_bytes": len(encoded),
        "reason": OVER_CASE_ITEM_LIMIT_REASON,
        "schema": "yoetz.bounded-content-omission/1",
    }
    return _structural_json(marker), True


def _history_json(
    item: FrozenHistoryEvent,
    *,
    include_content: bool,
    include_exact_command_text: bool,
) -> tuple[str, bool]:
    body: dict[str, JsonValue] = {
        "content_visibility": item.content_visibility,
        "event_id": item.event_id,
        "ingestion_sequence": item.ingestion_sequence,
        "kind": item.schema_name,
        "occurred_at": item.occurred_at,
        "payload_digest": item.payload_digest,
    }
    if item.accepted_at is None:
        body["occurred_at_consistency"] = "not_available_in_legacy_case"
    else:
        body["accepted_at"] = item.accepted_at
        body["occurred_at_consistency"] = item.occurred_at_consistency
    if include_content and item.payload is not None:
        payload = dict(cast(Mapping[str, JsonValue], item.payload))
        if item.schema_name == "action_recorded" and not include_exact_command_text:
            payload.pop("command", None)
        body["payload"] = cast(JsonValue, payload)
    return _bounded_json(body)


def _omit(
    subject_ref: str,
    category: DataCategory,
    source_kind: _SourceKind,
    reason: _OmissionReason,
) -> ReviewOmission:
    return ReviewOmission(
        subject_ref=subject_ref,
        category=category,
        source_kind=source_kind,
        reason=reason,
    )


def _match_assessments(
    case: DeterministicCase,
    findings: Sequence[Finding],
) -> tuple[tuple[Finding, DeterministicAssessment], ...]:
    assessments, _executions = run_deterministic_policies(
        case,
        CheckScope((), ()),
        _CANONICAL_PACKS,
    )
    by_key: dict[tuple[object, tuple[str, ...]], DeterministicAssessment] = {}
    for assessment in assessments:
        key = (
            assessment.candidate.kind,
            tuple(str(ref) for ref in assessment.candidate.subject_refs),
        )
        by_key[key] = assessment
    matched: list[tuple[Finding, DeterministicAssessment]] = []
    for finding in findings:
        key = (finding.kind, tuple(str(ref) for ref in finding.subject_refs))
        assessment = by_key.get(key)
        if assessment is not None:
            matched.append((finding, assessment))
    return tuple(matched)


def build_semantic_case(
    *,
    case_id: str,
    frozen_case: DeterministicCase,
    dependency_digest: str,
    findings: Sequence[Finding],
    review_context_profile: ReviewContextProfile,
    review_selection: ReviewSelectionPolicy,
    policy_id: str,
    policy_version: str,
) -> SemanticCase:
    """Build one pre-egress semantic case from frozen authority only."""

    if type(frozen_case) is not DeterministicCase:
        raise TypeError("deterministic_case_invalid")
    if type(review_context_profile) is not ReviewContextProfile:
        raise TypeError("review_context_profile_invalid")
    if type(review_selection) is not ReviewSelectionPolicy:
        raise TypeError("review_selection_invalid")
    if review_context_profile is not ReviewContextProfile.CUSTOM:
        expected = ReviewSelectionPolicy.for_profile(review_context_profile)
        if review_selection != expected:
            # Custom overlays may meet() narrower; allow any selection that is a meet of profile.
            meet = expected.meet(review_selection)
            if meet != review_selection:
                raise ValueError("review_selection_profile_mismatch")

    selection = review_selection
    sections = frozenset(selection.sections)
    frontier_refs = frozenset(str(ref) for ref in frozen_case.allowed_ids)
    local_check_refs = frozenset(str(item.finding_id) for item in findings)
    if frontier_refs & local_check_refs:
        # Local check IDs must not collide with frontier material.
        local_check_refs = frozenset(ref for ref in local_check_refs if ref not in frontier_refs)
    allowed = frontier_refs | local_check_refs
    projection = frozen_case.projection

    items: list[SemanticCaseItem] = []
    # Item ids whose recorded text was admitted by the publish-side prose bound and then could
    # not be carried whole by the case. Folded into the packet coverage below so the omission is
    # an author-visible fact rather than a silent shortening (issue #177).
    over_limit: set[str] = set()
    omissions: list[ReviewOmission] = []
    goal_ids: list[str] = []
    obligation_ids: list[str] = []
    claim_ids: list[str] = []
    decision_ids: list[str] = []
    timeline_ids: list[str] = []
    targeted: list[TargetedExcerptRef] = []
    changes: list[ChangeObservation] = []

    # --- Goal (latest plan summary) ---
    if projection.plans:
        latest_version = max(projection.plans)
        plan = projection.plans[latest_version]
        plan_ref = str(plan.source_event_id)
        if "goal" in sections and plan.payload is not None and not plan.redacted:
            text, content_omitted = _bounded_json(
                cast(Mapping[str, JsonValue], encode_payload(plan.payload))
            )
            item = _content_item(
                item_id=f"goal-{latest_version}",
                section="goal",
                category=DataCategory.TASK_DESCRIPTION,
                source_kind="task",
                source_ref=plan_ref,
                linked_subject_refs=(plan_ref,) if plan_ref in allowed else (),
                occurred_order=plan.source_frontier,
                text=text,
                over_limit=over_limit,
            )
            items.append(item)
            goal_ids.append(item.item_id)
            if content_omitted:
                over_limit.add(item.item_id)
                omissions.append(
                    _omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "not_selected")
                )
        elif "goal" not in sections:
            omissions.append(_omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "not_selected"))
        elif plan.redacted:
            omissions.append(
                _omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "redacted_never_send")
            )
        else:
            omissions.append(_omit(plan_ref, DataCategory.TASK_DESCRIPTION, "task", "not_recorded"))

    # --- Obligations ---
    for obligation_id, record in sorted(
        projection.obligations.items(), key=lambda pair: str(pair[0])
    ):
        ref = str(obligation_id)
        if ref not in allowed and str(record.source_event_id) not in allowed:
            continue
        source_ref = ref if ref in allowed else str(record.source_event_id)
        if "obligations" not in sections:
            omissions.append(
                _omit(source_ref, DataCategory.OBLIGATION_TEXT, "obligation", "not_selected")
            )
            continue
        payload = record.payload
        if record.redacted or payload is None:
            omissions.append(
                _omit(
                    source_ref,
                    DataCategory.OBLIGATION_TEXT,
                    "obligation",
                    "redacted_never_send" if record.redacted else "not_recorded",
                )
            )
            continue
        assert type(payload) is ObligationPublishedPayload
        linked = tuple(
            sorted(
                {
                    source_ref,
                    *(str(item) for item in payload.source_refs if str(item) in allowed),
                },
                key=str.encode,
            )
        )[:16]
        text, content_omitted = _bounded_json(
            cast(Mapping[str, JsonValue], encode_payload(payload))
        )
        item = _content_item(
            item_id=f"obligation-{ref}",
            section="obligation",
            category=DataCategory.OBLIGATION_TEXT,
            source_kind="obligation",
            source_ref=source_ref,
            linked_subject_refs=linked if linked else (source_ref,),
            occurred_order=record.source_frontier,
            text=text,
            over_limit=over_limit,
        )
        items.append(item)
        obligation_ids.append(item.item_id)
        if content_omitted:
            over_limit.add(item.item_id)
            omissions.append(
                _omit(source_ref, DataCategory.OBLIGATION_TEXT, "obligation", "not_selected")
            )

    # --- Claims ---
    for claim_id, record in sorted(projection.claims.items(), key=lambda pair: str(pair[0])):
        ref = str(claim_id)
        if ref not in allowed:
            continue
        if "claims" not in sections:
            omissions.append(_omit(ref, DataCategory.CLAIM_TEXT, "claim", "not_selected"))
            continue
        payload = record.payload
        if record.redacted or payload is None:
            omissions.append(
                _omit(
                    ref,
                    DataCategory.CLAIM_TEXT,
                    "claim",
                    "redacted_never_send" if record.redacted else "not_recorded",
                )
            )
            continue
        assert type(payload) is ClaimRecordedPayload
        text, content_omitted = _bounded_json(
            cast(Mapping[str, JsonValue], encode_payload(payload))
        )
        item = _content_item(
            item_id=f"claim-{ref}",
            section="claim",
            category=DataCategory.CLAIM_TEXT,
            source_kind="claim",
            source_ref=ref,
            linked_subject_refs=(ref,),
            occurred_order=record.source_frontier,
            text=text,
            over_limit=over_limit,
        )
        items.append(item)
        claim_ids.append(item.item_id)
        if content_omitted:
            over_limit.add(item.item_id)
            omissions.append(_omit(ref, DataCategory.CLAIM_TEXT, "claim", "not_selected"))

        if "change_observations" in sections and payload.subject_state is not None:
            changes.append(
                ChangeObservation(
                    subject_refs=(ref,),
                    claimed_change=True,
                    subject_state_relation=SubjectStateRelation.UNKNOWN,
                    content_visibility=(
                        "available" if "targeted_excerpts" in sections else "not_selected"
                    ),
                )
            )

    # --- Decisions ---
    for event_id, record in sorted(projection.decisions.items(), key=lambda pair: str(pair[0])):
        ref = str(event_id)
        if ref not in allowed:
            continue
        if "decisions" not in sections:
            omissions.append(_omit(ref, DataCategory.DECISION_EXCERPT, "decision", "not_selected"))
            continue
        payload = record.payload
        if record.redacted or payload is None:
            omissions.append(
                _omit(
                    ref,
                    DataCategory.DECISION_EXCERPT,
                    "decision",
                    "redacted_never_send" if record.redacted else "not_recorded",
                )
            )
            continue
        assert type(payload) is DecisionRecordedPayload
        text, content_omitted = _bounded_json(
            cast(Mapping[str, JsonValue], encode_payload(payload))
        )
        item = _content_item(
            item_id=f"decision-{ref}",
            section="decision",
            category=DataCategory.DECISION_EXCERPT,
            source_kind="decision",
            source_ref=ref,
            linked_subject_refs=(ref,),
            occurred_order=record.source_frontier,
            text=text,
            over_limit=over_limit,
        )
        items.append(item)
        decision_ids.append(item.item_id)
        if content_omitted:
            over_limit.add(item.item_id)
            omissions.append(_omit(ref, DataCategory.DECISION_EXCERPT, "decision", "not_selected"))

    # --- Frozen accepted-event history ---
    if "timeline" in sections and frozen_case.history_availability == "available":
        detailed_history = bool({"goal", "obligations", "claims", "decisions"} & sections)
        reserve_window_item = bool(frozen_case.history_omitted_before_count)
        history_limit = max(0, selection.max_timeline_items - (1 if reserve_window_item else 0))
        history_rows = frozen_case.history[-history_limit:] if history_limit else ()
        omitted_rows = frozen_case.history[: len(frozen_case.history) - len(history_rows)]
        for history_item in omitted_rows:
            source_kind, category = _HISTORY_KIND[history_item.schema_name]
            omissions.append(
                _omit(str(history_item.event_id), category, source_kind, "not_selected")
            )
        if reserve_window_item and selection.max_timeline_items:
            first_sequence = history_rows[0].ingestion_sequence if history_rows else 1
            item = _content_item(
                item_id="history-window",
                section="timeline",
                category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
                source_kind="task",
                source_ref="history-window",
                linked_subject_refs=(),
                occurred_order=max(0, first_sequence - 1),
                text=_structural_json(
                    {
                        "kind": "history_window",
                        "omitted_before_count": frozen_case.history_omitted_before_count,
                        "reason": "not_selected",
                    }
                ),
                over_limit=over_limit,
            )
            items.append(item)
            timeline_ids.append(item.item_id)
        for history_item in history_rows:
            source_kind, content_category = _HISTORY_KIND[history_item.schema_name]
            include_content = detailed_history and history_item.content_visibility == "available"
            category = (
                content_category if include_content else DataCategory.BOUNDED_STRUCTURAL_METADATA
            )
            text, content_omitted = _history_json(
                history_item,
                include_content=include_content,
                include_exact_command_text=selection.include_exact_command_text,
            )
            event_ref = str(history_item.event_id)
            item = _content_item(
                item_id=f"history-{event_ref}",
                section="timeline",
                category=category,
                source_kind=source_kind,
                source_ref=event_ref,
                linked_subject_refs=(event_ref,) if event_ref in allowed else (),
                occurred_order=history_item.ingestion_sequence,
                text=text,
                over_limit=over_limit,
            )
            items.append(item)
            timeline_ids.append(item.item_id)
            if history_item.content_visibility != "available":
                omissions.append(
                    _omit(
                        event_ref,
                        content_category,
                        source_kind,
                        history_item.content_visibility,
                    )
                )
            elif not detailed_history or content_omitted:
                if content_omitted:
                    over_limit.add(item.item_id)
                omissions.append(_omit(event_ref, content_category, source_kind, "not_selected"))

    if (
        "timeline" in sections
        and frozen_case.history_availability == "not_recorded"
        and projection.plans
    ):
        latest_plan = projection.plans[max(projection.plans)]
        omissions.append(
            _omit(
                str(latest_plan.source_event_id),
                DataCategory.BOUNDED_STRUCTURAL_METADATA,
                "task",
                "not_recorded",
            )
        )

    # --- Projection fallback timeline for legacy/synthetic frozen cases ---
    if "timeline" in sections and frozen_case.history_availability == "not_recorded":
        timeline_candidates: list[tuple[int, str, _SourceKind, str, Mapping[str, JsonValue]]] = []
        for obligation_id, record in projection.obligations.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "obligation",
                    str(obligation_id)
                    if str(obligation_id) in allowed
                    else str(record.source_event_id),
                    {
                        "kind": "obligation_published",
                        "obligation_id": str(obligation_id),
                        "source_event_id": str(record.source_event_id),
                        "status": (
                            record.payload.status.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for claim_id, record in projection.claims.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "claim",
                    str(claim_id),
                    {
                        "kind": "claim_recorded",
                        "claim_id": str(claim_id),
                        "source_event_id": str(record.source_event_id),
                        "claim_kind": (
                            record.payload.claim_kind.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for action_id, record in projection.actions.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "action",
                    str(action_id) if str(action_id) in allowed else str(record.source_event_id),
                    {
                        "kind": "action_recorded",
                        "action_id": str(action_id),
                        "source_event_id": str(record.source_event_id),
                        "action_kind": (
                            record.payload.action_kind.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for result_id, record in projection.results.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "result",
                    str(result_id) if str(result_id) in allowed else str(record.source_event_id),
                    {
                        "kind": "result_recorded",
                        "result_id": str(result_id),
                        "source_event_id": str(record.source_event_id),
                        "outcome": (
                            record.payload.outcome.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for evidence_id, record in projection.evidence.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "evidence",
                    str(evidence_id)
                    if str(evidence_id) in allowed
                    else str(record.source_event_id),
                    {
                        "kind": "evidence_recorded",
                        "evidence_id": str(evidence_id),
                        "source_event_id": str(record.source_event_id),
                        "evidence_kind": (
                            record.payload.evidence_kind.value
                            if record.payload is not None
                            else "unavailable"
                        ),
                    },
                )
            )
        for event_id, record in projection.decisions.items():
            timeline_candidates.append(
                (
                    record.source_frontier,
                    str(record.source_event_id),
                    "decision",
                    str(event_id),
                    {
                        "kind": "decision_recorded",
                        "source_event_id": str(event_id),
                    },
                )
            )
        # Always record the frozen frontier as a structural anchor.
        timeline_candidates.append(
            (
                frozen_case.frontier.sequence,
                f"frontier-{frozen_case.frontier.sequence}",
                "task",
                f"frontier-{frozen_case.frontier.sequence}",
                {
                    "kind": "frontier",
                    "sequence": frozen_case.frontier.sequence,
                    "head_digest": frozen_case.frontier.head_digest,
                    "dependency_digest": dependency_digest,
                },
            )
        )
        timeline_candidates.sort(
            key=lambda row: (row[0], row[1].encode("ascii"), row[3].encode("ascii"))
        )
        # item_id is timeline-{source_ref}; duplicate source_ref would invalidate the case.
        seen_timeline_refs: set[str] = set()
        deduped_timeline: list[tuple[int, str, _SourceKind, str, Mapping[str, JsonValue]]] = []
        for row in timeline_candidates:
            source_ref = row[3]
            if source_ref in seen_timeline_refs:
                continue
            seen_timeline_refs.add(source_ref)
            deduped_timeline.append(row)
        for order, _event, source_kind, source_ref, body in deduped_timeline[
            : selection.max_timeline_items
        ]:
            linked = (source_ref,) if source_ref in allowed else ()
            item = _content_item(
                item_id=f"timeline-{source_ref}",
                section="timeline",
                category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
                source_kind=source_kind,
                source_ref=source_ref,
                linked_subject_refs=linked,
                occurred_order=order,
                text=_structural_json(body),
                over_limit=over_limit,
            )
            items.append(item)
            timeline_ids.append(item.item_id)

    # --- Deterministic assessments + optional finding prose ---
    review_assessments: list[ReviewAssessment] = []
    if "deterministic_assessments" in sections:
        matched = _match_assessments(frozen_case, findings)
        for finding, assessment in matched[: selection.max_assessments]:
            summary_id: str | None = None
            detail_id: str | None = None
            if selection.include_finding_prose:
                finding_ref = str(finding.finding_id)
                linked = tuple(str(ref) for ref in finding.subject_refs)
                # Prose requires exact-match allowlist on every subject_ref; otherwise keep the
                # deterministic assessment without summary/detail content items.
                if linked and set(linked) <= allowed:
                    summary_id = f"finding-summary-{finding_ref}"
                    detail_id = f"finding-detail-{finding_ref}"
                    items.append(
                        _content_item(
                            item_id=summary_id,
                            section="deterministic_summary",
                            category=DataCategory.FINDING_SUMMARY,
                            source_kind="finding",
                            source_ref=finding_ref,
                            linked_subject_refs=linked,
                            occurred_order=finding.subject_frontier.sequence,
                            text=finding.summary,
                            over_limit=over_limit,
                        )
                    )
                    items.append(
                        _content_item(
                            item_id=detail_id,
                            section="deterministic_detail",
                            category=DataCategory.FINDING_SUMMARY,
                            source_kind="finding",
                            source_ref=finding_ref,
                            linked_subject_refs=linked,
                            occurred_order=finding.subject_frontier.sequence,
                            text=finding.detail,
                            over_limit=over_limit,
                        )
                    )
            projected = project_review_assessment(
                assessment,
                str(finding.finding_id),
                summary_item_id=summary_id,
                detail_item_id=detail_id,
            )
            if type(projected) is ReviewAssessment:
                review_assessments.append(projected)
            elif type(projected) is ReviewAssessmentSkipped:
                omissions.append(projected.omission)

    # --- Targeted excerpts (recorded text only; never fetch objects) ---
    excerpt_bytes_used = 0
    if "targeted_excerpts" in sections and selection.max_excerpts > 0:
        linked_subjects: set[str] = set()
        for finding in findings:
            linked_subjects.update(str(ref) for ref in finding.subject_refs)
        for claim_id in projection.claims:
            linked_subjects.add(str(claim_id))
        for obligation_id in projection.obligations:
            linked_subjects.add(str(obligation_id))
        # Assessment supporting refs are case-bound links the reviewer may need as excerpts.
        for assessment in review_assessments:
            linked_subjects.update(str(ref) for ref in assessment.supporting_refs)
            for fact in (*assessment.observed_facts, *assessment.required_but_missing_facts):
                linked_subjects.update(str(ref) for ref in fact.subject_refs)
        for claim_record in projection.claims.values():
            if claim_record.payload is None:
                continue
            linked_subjects.update(str(ref) for ref in claim_record.payload.supporting_refs)

        evidence_rows = sorted(projection.evidence.items(), key=lambda pair: str(pair[0]))
        for evidence_id, record in evidence_rows:
            if len(targeted) >= selection.max_excerpts:
                break
            ref = str(evidence_id)
            if ref not in allowed:
                continue
            payload = record.payload
            if payload is None or record.redacted:
                omissions.append(
                    _omit(
                        ref,
                        DataCategory.EVIDENCE_EXCERPT,
                        "evidence",
                        "redacted_never_send" if record.redacted else "not_recorded",
                    )
                )
                continue
            assert type(payload) is EvidenceRecordedPayload
            excerpt_kind = _EVIDENCE_EXCERPT_KIND.get(payload.evidence_kind, "evidence")
            if excerpt_kind not in selection.excerpt_kinds:
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
                )
                continue
            if selection.relevance == "linked_subjects_only":
                source_event = str(record.source_event_id)
                if ref not in linked_subjects and source_event not in linked_subjects:
                    omissions.append(
                        _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
                    )
                    continue
            digest_provenance: ExcerptDigestProvenance | None = None
            if payload.content_digest is not None:
                binding = payload.digest_binding
                if binding is None:
                    omissions.append(
                        _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_recorded")
                    )
                    continue
                digest_provenance = ExcerptDigestProvenance(
                    evidence_kind=payload.evidence_kind,
                    strength=payload.strength,
                    content_digest=payload.content_digest,
                    digest_subject=binding.subject,
                    content_availability=binding.content_availability,
                    byte_count=binding.byte_count,
                    provenance=binding.provenance,
                    approval_commitment=binding.approval_commitment,
                    approved_check_result_digest=binding.approved_check_result_digest,
                )
                if payload.description:
                    # Caller-authored narrative stays legible; digest identity rides on the
                    # excerpt ref instead of replacing the content (issue #176).
                    text = payload.description
                else:
                    text = canonical_encode(
                        cast(
                            JsonValue,
                            {
                                "schema": "yoetz.evidence-digest-provenance/1",
                                "evidence_kind": payload.evidence_kind.value,
                                "strength": payload.strength.value,
                                "content_digest": payload.content_digest,
                                "digest_subject": binding.subject.value,
                                "content_availability": binding.content_availability.value,
                                "byte_count": binding.byte_count,
                                "provenance": binding.provenance.value,
                                **(
                                    {}
                                    if binding.approval_commitment is None
                                    else {"approval_commitment": binding.approval_commitment}
                                ),
                                **(
                                    {}
                                    if binding.approved_check_result_digest is None
                                    else {
                                        "approved_check_result_digest": (
                                            binding.approved_check_result_digest
                                        )
                                    }
                                ),
                            },
                        )
                    ).decode("utf-8")
            else:
                text = payload.description or payload.reference
            if text is None or not text:
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_recorded")
                )
                continue
            encoded = text.encode("utf-8")
            excerpt_truncated = len(encoded) > selection.max_excerpt_bytes
            if excerpt_truncated:
                text = encoded[: selection.max_excerpt_bytes].decode("utf-8", errors="ignore")
                encoded = text.encode("utf-8")
            if excerpt_bytes_used + len(encoded) > selection.max_total_excerpt_bytes:
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
                )
                continue
            linked = tuple(
                sorted(
                    {
                        ref,
                        str(record.source_event_id),
                        *(
                            str(claim_id)
                            for claim_id, claim_record in projection.claims.items()
                            if claim_record.payload is not None
                            and any(
                                str(support) == ref
                                for support in claim_record.payload.supporting_refs
                            )
                        ),
                    }
                    & allowed,
                    key=str.encode,
                )
            )[:16]
            if not linked:
                omissions.append(
                    _omit(ref, DataCategory.EVIDENCE_EXCERPT, excerpt_kind, "not_selected")
                )
                continue
            # Clipping at the selection policy's excerpt bound is the author's declared choice
            # (the packet metadata carries max_excerpt_bytes); only _content_item's item bound
            # records the over-item-limit gap. A custom policy narrower than the item bound
            # must not read as a size failure.
            item_id = f"excerpt-{ref}"
            item = _content_item(
                item_id=item_id,
                section="excerpt",
                category=DataCategory.EVIDENCE_EXCERPT,
                source_kind=excerpt_kind,
                source_ref=ref,
                linked_subject_refs=linked,
                occurred_order=record.source_frontier,
                text=text,
                over_limit=over_limit,
            )
            items.append(item)
            targeted.append(
                TargetedExcerptRef(
                    excerpt_item_id=item_id,
                    source_kind=excerpt_kind,
                    linked_subject_refs=linked,
                    subject_state_relation=SubjectStateRelation.UNKNOWN,
                    content_visibility="available",
                    content_digest=item.content_digest,
                    content_bytes=item.content_bytes,
                    digest_provenance=digest_provenance,
                )
            )
            excerpt_bytes_used += item.content_bytes

        # Optional command excerpts from actions when expanded selection allows exact commands.
        if selection.include_exact_command_text and "command" in selection.excerpt_kinds:
            for action_id, record in sorted(
                projection.actions.items(), key=lambda pair: str(pair[0])
            ):
                if len(targeted) >= selection.max_excerpts:
                    break
                ref = str(action_id)
                if ref not in allowed or record.payload is None or record.redacted:
                    continue
                if record.payload.action_kind is not ActionKind.COMMAND:
                    continue
                command = record.payload.command
                if command is None:
                    omissions.append(
                        _omit(ref, DataCategory.COMMAND_METADATA, "command", "not_recorded")
                    )
                    continue
                encoded = command.encode("utf-8")
                command_truncated = len(encoded) > selection.max_excerpt_bytes
                if command_truncated:
                    command = encoded[: selection.max_excerpt_bytes].decode(
                        "utf-8", errors="ignore"
                    )
                    encoded = command.encode("utf-8")
                if excerpt_bytes_used + len(encoded) > selection.max_total_excerpt_bytes:
                    break
                linked = (ref,) if ref in allowed else (str(record.source_event_id),)
                linked = tuple(item for item in linked if item in allowed)
                if not linked:
                    continue
                item_id = f"excerpt-cmd-{ref}"
                item = _content_item(
                    item_id=item_id,
                    section="excerpt",
                    category=DataCategory.COMMAND_METADATA,
                    source_kind="command",
                    source_ref=ref,
                    linked_subject_refs=linked,
                    occurred_order=record.source_frontier,
                    text=command,
                    over_limit=over_limit,
                )
                items.append(item)
                targeted.append(
                    TargetedExcerptRef(
                        excerpt_item_id=item_id,
                        source_kind="command",
                        linked_subject_refs=linked,
                        subject_state_relation=SubjectStateRelation.UNKNOWN,
                        content_visibility="available",
                        content_digest=item.content_digest,
                        content_bytes=item.content_bytes,
                    )
                )
                excerpt_bytes_used += item.content_bytes

        # Failed results as failure excerpts when description-like summary exists.
        if "failure" in selection.excerpt_kinds:
            for result_id, record in sorted(
                projection.results.items(), key=lambda pair: str(pair[0])
            ):
                if len(targeted) >= selection.max_excerpts:
                    break
                ref = str(result_id)
                if ref not in allowed or record.payload is None or record.redacted:
                    continue
                if record.payload.outcome is not ResultOutcome.FAILURE:
                    continue
                summary = record.payload.summary
                if summary is None or not summary:
                    omissions.append(
                        _omit(ref, DataCategory.EVIDENCE_EXCERPT, "failure", "not_recorded")
                    )
                    continue
                encoded = summary.encode("utf-8")
                summary_truncated = len(encoded) > selection.max_excerpt_bytes
                if summary_truncated:
                    summary = encoded[: selection.max_excerpt_bytes].decode(
                        "utf-8", errors="ignore"
                    )
                    encoded = summary.encode("utf-8")
                if excerpt_bytes_used + len(encoded) > selection.max_total_excerpt_bytes:
                    omissions.append(
                        _omit(ref, DataCategory.EVIDENCE_EXCERPT, "failure", "not_selected")
                    )
                    continue
                linked = tuple(
                    item
                    for item in (ref, str(record.payload.action_id), str(record.source_event_id))
                    if item in allowed
                )[:16]
                if not linked:
                    continue
                item_id = f"excerpt-fail-{ref}"
                item = _content_item(
                    item_id=item_id,
                    section="excerpt",
                    category=DataCategory.EVIDENCE_EXCERPT,
                    source_kind="failure",
                    source_ref=ref,
                    linked_subject_refs=linked,
                    occurred_order=record.source_frontier,
                    text=summary,
                    over_limit=over_limit,
                )
                items.append(item)
                targeted.append(
                    TargetedExcerptRef(
                        excerpt_item_id=item_id,
                        source_kind="failure",
                        linked_subject_refs=linked,
                        subject_state_relation=SubjectStateRelation.UNKNOWN,
                        content_visibility="available",
                        content_digest=item.content_digest,
                        content_bytes=item.content_bytes,
                    )
                )
                excerpt_bytes_used += item.content_bytes

    # Cap lists per selection.
    goal_ids = goal_ids[:4]
    obligation_ids = obligation_ids[:32]
    claim_ids = claim_ids[:32]
    decision_ids = decision_ids[:16]
    timeline_ids = timeline_ids[: selection.max_timeline_items]
    review_assessments = review_assessments[: selection.max_assessments]
    changes = changes[: selection.max_change_observations]
    targeted = targeted[: selection.max_excerpts]

    kind_order = {
        kind: ordinal
        for ordinal, kind in enumerate(
            (
                FindingKind.COMPLETION_WITH_OPEN_OBLIGATIONS,
                FindingKind.REQUESTED_ITEM_NEVER_ATTEMPTED,
                FindingKind.FAILED_WORK_OMITTED,
                FindingKind.CLAIM_WITHOUT_ADMISSIBLE_EVIDENCE,
                FindingKind.RESULT_WITHOUT_ACTION,
                FindingKind.ACTION_WITHOUT_RESULT,
                FindingKind.STALE_EVIDENCE_FOR_CHANGED_STATE,
                FindingKind.CONTRADICTORY_CLAIMS_UNRESOLVED,
                FindingKind.LEDGER_STALE_OR_INCOMPLETE,
                FindingKind.WEAK_OR_STALE_RESPONSE,
                FindingKind.EVIDENCE_DOES_NOT_SUPPORT_CLAIM,
                FindingKind.DIFF_DOES_NOT_MATCH_ACCOUNT,
                FindingKind.MATERIAL_LIMITATION_OMITTED,
                FindingKind.QUESTIONABLE_FINDING_REJECTION,
            )
        )
    }
    review_assessments.sort(
        key=lambda item: (
            kind_order[item.finding_kind],
            tuple(ref.encode("ascii") for ref in item.subject_refs),
        )
    )
    changes.sort(key=lambda item: tuple(ref.encode("ascii") for ref in item.subject_refs))
    omissions = sorted(
        set(omissions),
        key=lambda item: (
            item.subject_ref.encode("ascii"),
            item.category.value.encode("ascii"),
            item.reason.encode("ascii"),
        ),
    )[: selection.max_omissions]

    # Keep only items that remain referenced after caps.
    keep_ids = (
        set(goal_ids) | set(obligation_ids) | set(claim_ids) | set(decision_ids) | set(timeline_ids)
    )
    for assessment in review_assessments:
        if assessment.summary_item_id is not None and assessment.detail_item_id is not None:
            keep_ids.add(assessment.summary_item_id)
            keep_ids.add(assessment.detail_item_id)
    for excerpt in targeted:
        keep_ids.add(excerpt.excerpt_item_id)

    items = [item for item in items if item.item_id in keep_ids]
    # Sort items per SemanticCase order.
    _SECTION_ORDINAL = {
        "goal": 0,
        "obligation": 1,
        "claim": 2,
        "decision": 3,
        "timeline": 4,
        "deterministic_summary": 5,
        "deterministic_detail": 6,
        "excerpt": 7,
    }
    items.sort(
        key=lambda item: (
            _SECTION_ORDINAL[item.section],
            item.occurred_order,
            item.source_ref.encode("ascii"),
            item.item_id.encode("ascii"),
        )
    )

    if not items:
        # Empty frozen case: still emit a frontier structural item so the case is valid.
        body = {
            "kind": "frontier",
            "sequence": frozen_case.frontier.sequence,
            "head_digest": frozen_case.frontier.head_digest,
            "dependency_digest": dependency_digest,
        }
        item = _content_item(
            item_id="timeline-frontier",
            section="timeline",
            category=DataCategory.BOUNDED_STRUCTURAL_METADATA,
            source_kind="task",
            source_ref=f"frontier-{frozen_case.frontier.sequence}",
            linked_subject_refs=(),
            occurred_order=frozen_case.frontier.sequence,
            text=_structural_json(body),
            over_limit=over_limit,
        )
        items = [item]
        timeline_ids = [item.item_id]

    coverage = case_coverage(frozen_case, semantic=True)
    # Count only overflow on items the caps kept: an item dropped downstream is already disclosed
    # as an omission, and naming it here would report a shortening the reviewer never saw.
    if over_limit & {item.item_id for item in items}:
        coverage = replace(
            coverage,
            ledger_freshness=(
                LedgerFreshness.PARTIAL
                if coverage.ledger_freshness is LedgerFreshness.CURRENT
                else coverage.ledger_freshness
            ),
            known_gaps=tuple(
                sorted(
                    {*coverage.known_gaps, SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP},
                    key=str.encode,
                )
            ),
        )
    packet = ReviewPacket(
        goal_item_ids=tuple(goal_ids),
        obligation_item_ids=tuple(obligation_ids),
        claim_item_ids=tuple(claim_ids),
        decision_item_ids=tuple(decision_ids),
        timeline_item_ids=tuple(timeline_ids),
        deterministic_assessments=tuple(review_assessments),
        change_observations=tuple(changes),
        coverage=coverage,
        targeted_excerpts=tuple(targeted),
        omissions=tuple(omissions),
    )

    selection_digest = review_selection_digest(selection)
    # Bind assessments/omissions/packet lists into the digest so provenance covers the full case.
    case_digest = canonical_digest(
        cast(
            JsonValue,
            {
                "dependency_digest": dependency_digest,
                "frontier_refs": sorted(frontier_refs),
                "items": [
                    {
                        "content_digest": item.content_digest,
                        "item_id": item.item_id,
                        "section": item.section,
                    }
                    for item in items
                ],
                "local_check_refs": sorted(local_check_refs),
                "policy_id": policy_id,
                "policy_version": policy_version,
                "question_set": list(_QUESTION_SET),
                "review_context_profile": review_context_profile.value,
                "review_packet": _packet_to_json(packet),
                "review_selection_digest": selection_digest,
                "schema": "yoetz.semantic-case/1",
                "subject_frontier": dict(frozen_case.frontier.as_wire()),
            },
        )
    )

    return SemanticCase(
        case_id=case_id,
        subject_frontier=frozen_case.frontier,
        dependency_digest=dependency_digest,
        frontier_refs=frontier_refs,
        local_check_refs=local_check_refs,
        review_context_profile=review_context_profile,
        review_selection=selection,
        policy_id=policy_id,
        policy_version=policy_version,
        packet=packet,
        items=tuple(items),
        question_set=_QUESTION_SET,
        case_digest=case_digest,
    )


def _assessment_to_json(assessment: ReviewAssessment) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "coverage_gaps": list(assessment.coverage_gaps),
        "finding_kind": assessment.finding_kind.value,
        "finding_ref": str(assessment.finding_ref),
        "observed_facts": [
            {"fact_code": fact.fact_code, "subject_refs": list(fact.subject_refs)}
            for fact in assessment.observed_facts
        ],
        "priority": assessment.priority,
        "required_but_missing_facts": [
            {"fact_code": fact.fact_code, "subject_refs": list(fact.subject_refs)}
            for fact in assessment.required_but_missing_facts
        ],
        "rule_id": assessment.rule_id,
        "source_availability": assessment.source_availability.value,
        "subject_refs": list(assessment.subject_refs),
        "subject_state_relation": assessment.subject_state_relation.value,
        "supporting_refs": list(assessment.supporting_refs),
    }
    if assessment.summary_item_id is not None and assessment.detail_item_id is not None:
        body["summary_item_id"] = assessment.summary_item_id
        body["detail_item_id"] = assessment.detail_item_id
    return body


def _digest_provenance_json(provenance: ExcerptDigestProvenance) -> dict[str, JsonValue]:
    return {
        "byte_count": provenance.byte_count,
        "content_availability": provenance.content_availability.value,
        "content_digest": provenance.content_digest,
        "digest_subject": provenance.digest_subject.value,
        "evidence_kind": provenance.evidence_kind.value,
        "provenance": provenance.provenance.value,
        "strength": provenance.strength.value,
        **(
            {}
            if provenance.approval_commitment is None
            else {"approval_commitment": provenance.approval_commitment}
        ),
        **(
            {}
            if provenance.approved_check_result_digest is None
            else {"approved_check_result_digest": provenance.approved_check_result_digest}
        ),
    }


def _packet_to_json(packet: ReviewPacket) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "change_observations": [
                {
                    "claimed_change": item.claimed_change,
                    "content_visibility": item.content_visibility,
                    "subject_refs": list(item.subject_refs),
                    "subject_state_relation": item.subject_state_relation.value,
                    **(
                        {
                            "after_state_digest": item.after_state_digest,
                            "before_state_digest": item.before_state_digest,
                        }
                        if item.before_state_digest is not None
                        else {}
                    ),
                }
                for item in packet.change_observations
            ],
            "claim_item_ids": list(packet.claim_item_ids),
            "coverage": coverage_to_json(packet.coverage),
            "decision_item_ids": list(packet.decision_item_ids),
            "deterministic_assessments": [
                _assessment_to_json(item) for item in packet.deterministic_assessments
            ],
            "goal_item_ids": list(packet.goal_item_ids),
            "obligation_item_ids": list(packet.obligation_item_ids),
            "omissions": [
                {
                    "category": item.category.value,
                    "reason": item.reason,
                    "source_kind": item.source_kind,
                    "subject_ref": item.subject_ref,
                }
                for item in packet.omissions
            ],
            "targeted_excerpts": [
                {
                    "content_bytes": item.content_bytes,
                    "content_digest": item.content_digest,
                    "content_visibility": item.content_visibility,
                    **(
                        {}
                        if item.digest_provenance is None
                        else {"digest_provenance": _digest_provenance_json(item.digest_provenance)}
                    ),
                    "excerpt_item_id": item.excerpt_item_id,
                    "linked_subject_refs": list(item.linked_subject_refs),
                    "source_kind": item.source_kind,
                    "subject_state_relation": item.subject_state_relation.value,
                }
                for item in packet.targeted_excerpts
            ],
            "timeline_item_ids": list(packet.timeline_item_ids),
        },
    )


def _item_catalog_json(items: Sequence[SemanticCaseItem]) -> list[dict[str, JsonValue]]:
    """Metadata-only catalog so egress can project without reverse-engineering origin_ref."""

    return [
        cast(
            dict[str, JsonValue],
            {
                "category": item.category.value,
                "content_bytes": item.content_bytes,
                "content_digest": item.content_digest,
                "item_id": item.item_id,
                "linked_subject_refs": list(item.linked_subject_refs),
                "section": item.section,
                "source_kind": item.source_kind,
                "source_ref": item.source_ref,
            },
        )
        for item in items
    ]


def _case_envelope_json(case: SemanticCase) -> dict[str, JsonValue]:
    return cast(
        dict[str, JsonValue],
        {
            "case_digest": case.case_digest,
            "case_id": case.case_id,
            "dependency_digest": case.dependency_digest,
            "frontier_refs": sorted(case.frontier_refs),
            "item_catalog": _item_catalog_json(case.items),
            "local_check_refs": sorted(case.local_check_refs),
            "policy_id": case.policy_id,
            "policy_version": case.policy_version,
            "question_set": list(case.question_set),
            "review_context_profile": case.review_context_profile.value,
            "review_packet": _packet_to_json(case.packet),
            "review_selection_digest": review_selection_digest(case.review_selection),
            "schema": _PACKET_SCHEMA,
            "subject_frontier": dict(case.subject_frontier.as_wire()),
        },
    )


def assemble_filtered_review_packet(
    envelope: Mapping[str, object],
    *,
    content_by_id: Mapping[str, bytes],
    included_item_ids: frozenset[str] | set[str],
) -> bytes:
    """Assemble ``yoetz.review-packet-case/1`` from a builder envelope + approved content.

    Single shared projection used by the application prepared-payload path and the privacy
    enforcer. Filters by approved item id only; never re-derives section/source metadata from
    origin pointers.
    """

    included = set(included_item_ids)
    frontier_raw = envelope.get("frontier_refs")
    local_raw = envelope.get("local_check_refs")
    frontier_refs: set[str] = (
        {item for item in cast(list[object], frontier_raw) if type(item) is str}
        if type(frontier_raw) is list
        else set()
    )
    local_check_refs: set[str] = (
        {item for item in cast(list[object], local_raw) if type(item) is str}
        if type(local_raw) is list
        else set()
    )
    allowed: set[str] = frontier_refs | local_check_refs

    catalog_raw = envelope.get("item_catalog")
    catalog: list[dict[str, object]] = []
    if type(catalog_raw) is list:
        for row in cast(list[object], catalog_raw):
            if isinstance(row, dict):
                catalog.append(cast(dict[str, object], row))

    content_rows: list[dict[str, JsonValue]] = []
    omitted_extra: list[dict[str, JsonValue]] = []
    for meta in catalog:
        item_id = meta.get("item_id")
        if type(item_id) is not str or item_id == REVIEW_PACKET_ITEM_ID:
            continue
        category = meta.get("category")
        source_kind = meta.get("source_kind")
        source_ref = meta.get("source_ref")
        section = meta.get("section")
        linked_raw = meta.get("linked_subject_refs")
        linked = (
            [ref for ref in cast(list[object], linked_raw) if type(ref) is str]
            if type(linked_raw) is list
            else []
        )
        if item_id in included and item_id in content_by_id:
            plaintext = content_by_id[item_id]
            try:
                text = plaintext.decode("utf-8")
            except UnicodeDecodeError:
                continue
            content_rows.append(
                cast(
                    dict[str, JsonValue],
                    {
                        "category": category if type(category) is str else "",
                        "content": text,
                        "content_bytes": len(plaintext),
                        "content_digest": "sha256:" + hashlib.sha256(plaintext).hexdigest(),
                        "item_id": item_id,
                        "linked_subject_refs": linked,
                        "section": section if type(section) is str else "timeline",
                        "source_kind": source_kind if type(source_kind) is str else "task",
                        "source_ref": source_ref if type(source_ref) is str else item_id,
                    },
                )
            )
            continue
        if item_id in included:
            continue
        # Prefer allowlisted source_ref, then any allowlisted linked ref; skip only if none.
        subject: str | None = None
        if type(source_ref) is str and source_ref in allowed:
            subject = source_ref
        else:
            for ref in linked:
                if ref in allowed:
                    subject = ref
                    break
        if subject is None:
            continue
        omitted_extra.append(
            cast(
                dict[str, JsonValue],
                {
                    "category": category if type(category) is str else "",
                    "reason": "withheld_by_policy",
                    "source_kind": source_kind if type(source_kind) is str else "task",
                    "subject_ref": subject,
                },
            )
        )
    content_rows.sort(key=lambda row: cast(str, row["item_id"]).encode("ascii"))

    packet_raw = envelope.get("review_packet")
    packet_obj: dict[str, JsonValue] = (
        cast(dict[str, JsonValue], dict(cast(dict[object, object], packet_raw)))
        if isinstance(packet_raw, dict)
        else {}
    )

    # Filter by what the document will actually carry — approved *and* catalogued. Filtering by
    # approval alone let an id survive here whose catalog row bounding had already removed, so the
    # packet pointed at an item absent from ``items``.
    carried = {cast(str, row["item_id"]) for row in content_rows}

    def _filter_ids(raw: object) -> list[JsonValue]:
        if type(raw) is not list:
            return []
        return [
            item_id
            for item_id in cast(list[object], raw)
            if type(item_id) is str and item_id in carried
        ]

    for key in _PACKET_ID_LIST_KEYS:
        packet_obj[key] = _filter_ids(packet_obj.get(key))

    excerpts_raw = packet_obj.get("targeted_excerpts")
    if type(excerpts_raw) is list:
        packet_obj["targeted_excerpts"] = cast(
            JsonValue,
            [
                row
                for row in cast(list[object], excerpts_raw)
                if isinstance(row, dict)
                and cast(dict[str, object], row).get("excerpt_item_id") in carried
            ],
        )
    else:
        packet_obj["targeted_excerpts"] = cast(JsonValue, [])

    assessments_raw = packet_obj.get("deterministic_assessments")
    filtered_assessments: list[JsonValue] = []
    if type(assessments_raw) is list:
        for raw in cast(list[object], assessments_raw):
            if not isinstance(raw, dict):
                continue
            row = dict(cast(dict[str, JsonValue], cast(dict[object, object], raw)))
            summary = row.get("summary_item_id")
            detail = row.get("detail_item_id")
            if type(summary) is str and type(detail) is str:
                if summary not in carried or detail not in carried:
                    row.pop("summary_item_id", None)
                    row.pop("detail_item_id", None)
            filtered_assessments.append(row)
    packet_obj["deterministic_assessments"] = filtered_assessments

    base_omissions: list[dict[str, JsonValue]] = []
    omissions_raw = packet_obj.get("omissions")
    if type(omissions_raw) is list:
        for raw in cast(list[object], omissions_raw):
            if isinstance(raw, dict):
                base_omissions.append(
                    cast(dict[str, JsonValue], dict(cast(dict[object, object], raw)))
                )

    seen: set[tuple[str, str, str]] = set()
    omissions: list[JsonValue] = []
    for row in [*base_omissions, *omitted_extra]:
        subject_ref = row.get("subject_ref")
        category = row.get("category")
        reason = row.get("reason")
        if type(subject_ref) is not str or type(category) is not str or type(reason) is not str:
            continue
        key = (subject_ref, category, reason)
        if key in seen:
            continue
        if subject_ref not in allowed:
            continue
        seen.add(key)
        omissions.append(row)
    omissions.sort(
        key=lambda row: (
            cast(str, cast(dict[str, JsonValue], row)["subject_ref"]).encode("ascii"),
            cast(str, cast(dict[str, JsonValue], row)["category"]).encode("ascii"),
            cast(str, cast(dict[str, JsonValue], row)["reason"]).encode("ascii"),
        )
    )
    packet_obj["omissions"] = omissions

    # Preserve change_observations / coverage as supplied by the builder envelope.
    if "change_observations" not in packet_obj:
        packet_obj["change_observations"] = cast(JsonValue, [])
    if "coverage" not in packet_obj:
        packet_obj["coverage"] = cast(JsonValue, {})

    # Approved content whose catalog row is absent cannot be described (the catalog *is* its
    # metadata), so it cannot travel. Counting it keeps the omission visible instead of letting
    # the packet read as though that material was never approved.
    uncatalogued = sum(
        1
        for item_id in included
        if item_id != REVIEW_PACKET_ITEM_ID and item_id in content_by_id and item_id not in carried
    )
    accounting_raw = envelope.get("selection_accounting")
    accounting: dict[str, JsonValue] = (
        cast(dict[str, JsonValue], dict(cast(dict[object, object], accounting_raw)))
        if isinstance(accounting_raw, dict)
        else {
            "assessment_links_stripped_count": "0",
            "catalog_dropped_count": "0",
            "change_observations_dropped_count": "0",
            "deterministic_assessments_dropped_count": "0",
            "omissions_dropped_count": "0",
            "reason": "not_minimized",
            "targeted_excerpts_dropped_count": "0",
        }
    )
    accounting["uncatalogued_approved_count"] = str(uncatalogued)

    document = cast(
        dict[str, JsonValue],
        {
            "case_digest": envelope.get("case_digest", ""),
            "case_id": envelope.get("case_id", ""),
            # The exact ids post-validation will accept in a challenge's cited_refs, in one place
            # the reviewer can read. The packet already carried them, split across frontier_refs
            # and local_check_refs, while items[].item_id — the ids most visible in the document —
            # are not citable at all. Naming the accept set explicitly is what lets a reviewer cite
            # correctly instead of guessing and having the challenge dropped.
            "citable_refs": sorted(frontier_refs | local_check_refs),
            "selection_accounting": cast(JsonValue, accounting),
            "dependency_digest": envelope.get("dependency_digest", ""),
            "frontier_refs": sorted(frontier_refs),
            "items": content_rows,
            "local_check_refs": sorted(local_check_refs),
            "policy_id": envelope.get("policy_id", ""),
            "policy_version": envelope.get("policy_version", ""),
            "question_set": (
                list(cast(list[object], envelope["question_set"]))
                if type(envelope.get("question_set")) is list
                else []
            ),
            "review_context_profile": envelope.get("review_context_profile", ""),
            "review_packet": packet_obj,
            "review_selection_digest": envelope.get("review_selection_digest", ""),
            "schema": _PACKET_SCHEMA,
            "subject_frontier": (
                dict(cast(dict[object, object], envelope["subject_frontier"]))
                if isinstance(envelope.get("subject_frontier"), dict)
                else {}
            ),
        },
    )
    return canonical_encode(cast(JsonValue, document))


class SemanticCaseTooLarge(ValueError):
    """The structural envelope cannot be reduced within its bound.

    Raised only when the irreducible core alone exceeds ``MAX_EGRESS_ENVELOPE_BYTES``. Every
    droppable row has already been removed and accounted for by then, so this is a genuine
    "this case cannot be reviewed", not a transient coordinator fault. Callers must map it to a
    terminal semantic outcome rather than swallowing it as an unexpected exception.
    """


def _catalog_rows(envelope: Mapping[str, JsonValue]) -> list[dict[str, JsonValue]]:
    raw = envelope.get("item_catalog")
    if type(raw) is not list:
        return []
    return [
        cast(dict[str, JsonValue], row) for row in cast(list[object], raw) if isinstance(row, dict)
    ]


def _catalog_item_ids(envelope: Mapping[str, JsonValue]) -> frozenset[str]:
    ids: set[str] = set()
    for row in _catalog_rows(envelope):
        item_id = row.get("item_id")
        if type(item_id) is str:
            ids.add(item_id)
    return frozenset(ids)


def _drop_catalog_row(envelope: dict[str, JsonValue]) -> bool:
    """Remove the lowest-priority catalog row and every reference that would dangle.

    Catalog order is the case's own section/occurrence order, so the tail is the least
    structurally load-bearing row. Dropping a row without also dropping its ids would leave
    ``*_item_ids`` and ``targeted_excerpts`` pointing at an item the packet no longer carries.
    """

    rows = _catalog_rows(envelope)
    if not rows:
        return False
    dropped = rows.pop()
    envelope["item_catalog"] = cast(JsonValue, rows)
    dropped_id = dropped.get("item_id")
    if type(dropped_id) is not str:
        return True
    packet_obj = envelope.get("review_packet")
    if not isinstance(packet_obj, dict):
        return True
    for key in _PACKET_ID_LIST_KEYS:
        current = packet_obj.get(key)
        if type(current) is list:
            packet_obj[key] = cast(
                JsonValue,
                [
                    value
                    for value in cast(list[object], current)
                    if not (type(value) is str and value == dropped_id)
                ],
            )
    excerpts = packet_obj.get("targeted_excerpts")
    if type(excerpts) is list:
        packet_obj["targeted_excerpts"] = cast(
            JsonValue,
            [
                row
                for row in cast(list[object], excerpts)
                if not (
                    isinstance(row, dict)
                    and cast(dict[str, object], row).get("excerpt_item_id") == dropped_id
                )
            ],
        )
    for key in ("summary_item_id", "detail_item_id"):
        assessments = packet_obj.get("deterministic_assessments")
        if type(assessments) is not list:
            continue
        for raw in cast(list[object], assessments):
            if isinstance(raw, dict) and cast(dict[str, object], raw).get(key) == dropped_id:
                cast(dict[str, object], raw).pop(key, None)
    return True


def _drop_packet_row(envelope: dict[str, JsonValue], key: str) -> bool:
    packet_obj = envelope.get("review_packet")
    if not isinstance(packet_obj, dict):
        return False
    rows = packet_obj.get(key)
    if type(rows) is not list or not rows:
        return False
    packet_obj[key] = cast(JsonValue, cast(list[object], rows)[:-1])
    return True


def _strip_assessment_links(envelope: dict[str, JsonValue]) -> int:
    """Drop assessment item-id links, which duplicate ids the catalog already carries."""

    packet_obj = envelope.get("review_packet")
    if not isinstance(packet_obj, dict):
        return 0
    rows = packet_obj.get("deterministic_assessments")
    if type(rows) is not list:
        return 0
    removed = 0
    for raw in cast(list[object], rows):
        if not isinstance(raw, dict):
            continue
        row = cast(dict[str, object], raw)
        for key in ("summary_item_id", "detail_item_id"):
            if row.pop(key, None) is not None:
                removed += 1
    return removed


def _set_selection_accounting(
    envelope: dict[str, JsonValue], reductions: Mapping[str, int]
) -> None:
    """Record what bounding removed, so a truncated packet can never read as a complete one."""

    minimized = any(count > 0 for count in reductions.values())
    envelope["selection_accounting"] = cast(
        JsonValue,
        {
            "assessment_links_stripped_count": str(reductions["assessment_links_stripped_count"]),
            "catalog_dropped_count": str(reductions["catalog_dropped_count"]),
            "change_observations_dropped_count": str(
                reductions["change_observations_dropped_count"]
            ),
            "deterministic_assessments_dropped_count": str(
                reductions["deterministic_assessments_dropped_count"]
            ),
            "omissions_dropped_count": str(reductions["omissions_dropped_count"]),
            "reason": "size_minimized" if minimized else "not_minimized",
            "targeted_excerpts_dropped_count": str(reductions["targeted_excerpts_dropped_count"]),
        },
    )


def bounded_case_envelope(case: SemanticCase) -> bytes:
    """Canonical case envelope guaranteed to fit ``MAX_EGRESS_ENVELOPE_BYTES``.

    The previous implementation was a fixed three-stage ladder whose last stage truncated the
    catalog to 64 rows — dead code, because a ``SemanticCase`` already admits at most 64 items.
    A real 44 KiB case therefore reduced to 38 KiB and then raised, stranding the whole check.

    This reduces one row at a time in a declared priority order and re-encodes, so it terminates:
    every step strictly shrinks the document, and the loop ends when the irreducible core is all
    that remains. Anything the catalog loses is counted in ``selection_accounting`` so the reader
    of the packet — and the receipt derived from it — sees that the case was minimized.
    """

    envelope = _case_envelope_json(case)
    reductions = {
        "assessment_links_stripped_count": 0,
        "catalog_dropped_count": 0,
        "change_observations_dropped_count": 0,
        "deterministic_assessments_dropped_count": 0,
        "omissions_dropped_count": 0,
        "targeted_excerpts_dropped_count": 0,
    }
    _set_selection_accounting(envelope, reductions)
    encoded = canonical_encode(cast(JsonValue, envelope))
    if len(encoded) <= MAX_EGRESS_ENVELOPE_BYTES:
        return encoded

    reductions["assessment_links_stripped_count"] += _strip_assessment_links(envelope)
    reducers: tuple[tuple[str, Callable[[dict[str, JsonValue]], bool]], ...] = (
        (
            "change_observations_dropped_count",
            lambda env: _drop_packet_row(env, "change_observations"),
        ),
        (
            "targeted_excerpts_dropped_count",
            lambda env: _drop_packet_row(env, "targeted_excerpts"),
        ),
        ("omissions_dropped_count", lambda env: _drop_packet_row(env, "omissions")),
        (
            "deterministic_assessments_dropped_count",
            lambda env: _drop_packet_row(env, "deterministic_assessments"),
        ),
        ("catalog_dropped_count", _drop_catalog_row),
    )
    while True:
        _set_selection_accounting(envelope, reductions)
        encoded = canonical_encode(cast(JsonValue, envelope))
        if len(encoded) <= MAX_EGRESS_ENVELOPE_BYTES:
            return encoded
        for accounting_key, reduce in reducers:
            if reduce(envelope):
                reductions[accounting_key] += 1
                break
        else:
            raise SemanticCaseTooLarge("semantic_case_envelope_too_large")


def semantic_case_to_candidate_context(
    case: SemanticCase,
    *,
    request_id: str,
    scope: AuthorizationScope,
    provider_binding: ProviderBinding,
) -> CandidateContext:
    """Project a semantic case into separate privacy-classified candidate items."""

    if type(case) is not SemanticCase:
        raise TypeError("semantic_case_invalid")
    envelope = bounded_case_envelope(case)
    # The catalog is the authority for what the provider payload may carry, so an item bounding
    # removed from the catalog must not travel as a candidate item either. Offering content whose
    # catalog row is gone would get it approved by privacy and then silently discarded during
    # assembly — the packet would claim coverage it never had.
    catalogued = _catalog_item_ids(cast(Mapping[str, JsonValue], strict_json_parse(envelope)))

    items: list[CandidateContextItem] = [
        CandidateContextItem(
            REVIEW_PACKET_ITEM_ID,
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            scope,
            "/case/review-packet",
            envelope,
        )
    ]
    for item in case.items:
        if item.item_id not in catalogued:
            continue
        items.append(
            CandidateContextItem(
                item.item_id,
                item.category,
                scope,
                f"/case/{item.section}/{item.item_id}",
                item.content,
            )
        )
    return CandidateContext(
        request_id=request_id,
        channel=EgressChannel.LLM_INFERENCE,
        local_sink=None,
        purpose=SEMANTIC_REVIEW_PURPOSE,
        scope=scope,
        subject_digest=case.case_digest,
        provider_binding=provider_binding,
        items=tuple(items),
    )


def semantic_case_to_prepared_payload(
    case: SemanticCase,
    included_item_ids: frozenset[str] | set[str],
) -> bytes:
    """Assemble the provider-facing review-packet document from privacy-approved items."""

    if type(case) is not SemanticCase:
        raise TypeError("semantic_case_invalid")
    content_by_id = {item.item_id: item.content for item in case.items}
    # Assemble from the same bounded envelope that was offered for authorization, never the
    # unbounded one: the prepared payload must describe exactly what privacy approved.
    envelope = cast(Mapping[str, JsonValue], strict_json_parse(bounded_case_envelope(case)))
    return assemble_filtered_review_packet(
        envelope,
        content_by_id=content_by_id,
        included_item_ids=included_item_ids,
    )
