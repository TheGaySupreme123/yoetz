"""Proof-based finding resolution: which later check may resolve which recorded finding.

A finding is a historical fact and stays visible forever. Whether it is *current* is a separate
fact, and only one kind of evidence may change it: a later local check whose recorded
state contains the finding, whose matching policy pack ran to completion with nothing suppressed,
whose scope covers the finding's subject, whose coverage carries no weakening gap for the
finding's proof class, and which did not return the same issue again. A closed local-only
exception lets case-wide host-observation limitations remain on the receipt without vetoing clean
structured-ledger proof. A response disposition never resolves a finding; it only answers it on
the record. Weak, skipped, failed, capped, stale, unreadable, or non-overlapping checks do nothing,
and nothing here ever strengthens coverage.

Everything in this module is pure and replay-derived, so a receipt, a status counter, and a
projection checkpoint all read the same fact.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import replace
from typing import Final

from yoetz.domain.coordination import CoordinationGapCode
from yoetz.domain.events import CheckRecordedPayload, ClaimKind, LedgerRecord, RequestedItemKind
from yoetz.domain.findings import Finding, FindingKind, FindingOrigin, ResponseDisposition
from yoetz.domain.receipts import (
    OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
    OPTIONAL_SEMANTIC_REVIEW_REGISTRATION_DRIFT_GAP,
    SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP,
    SEMANTIC_CHALLENGES_REJECTED_GAP,
    SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
    SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP,
    SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
    SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
)
from yoetz.domain.values import EventId, FindingId, ResultId
from yoetz.kernel.claims import effective_claim_items
from yoetz.kernel.plan_scope import current_plan_scope
from yoetz.kernel.projections import FindingProjectionRecord, ProjectionState
from yoetz.protocol.coverage import LedgerFreshness
from yoetz.protocol.models import SemanticReason, SemanticStatus

__all__ = [
    "IssueKey",
    "apply_check_resolution",
    "finding_is_resolved",
    "issue_key",
    "qualifying_check_resolves",
    "reopen_findings_resolved_by",
    "resolved_finding_ids",
]

IssueKey = tuple[object, ...]

# Coverage gaps that describe only the AI-powered review's own absence or weakness. A
# local finding is proven absent by the local pack that owns it, so these gaps
# do not weaken that proof; for an AI-powered finding they do, because the AI-powered review is the
# proof. The registration-drift gap is one of these: it rides alongside the ceiling gap on a
# strict check served while the applied record says policy, so a drift check still resolves
# local findings exactly like a plain ceiling check does (issue #537).
_SEMANTIC_ONLY_GAPS: Final = frozenset(
    {
        SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
        SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
        OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
        OPTIONAL_SEMANTIC_REVIEW_REGISTRATION_DRIFT_GAP,
        SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP,
        SEMANTIC_CHALLENGES_REJECTED_GAP,
        SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP,
    }
)
# Evidence-strength gaps: the cited evidence was readable but its content was not captured or
# was withheld, or its digest subject predates typed bindings. They bound how strong a receipt
# can be — the receipt keeps reporting them — but they do not stop a policy pack from reading
# the ledger state it judges, so they do not weaken the proof that an issue no longer fires.
# Every other gap (redacted or unavailable payloads, redacted objects, missing refs, unknown
# events, completion-scope gaps, import ranges, and any code not named here) means the check could
# not read or bound the material, and blocks both proof classes. The set is closed on purpose: a
# new gap code blocks resolution until someone decides otherwise here. Command gaps are never
# added to this set: the separately proved subject partition below is their only exception.
_EVIDENCE_STRENGTH_GAPS: Final = frozenset(
    {
        "evidence_content_digest_only",
        "evidence_content_withheld",
        "evidence_digest_subject_legacy_unknown",
    }
)
# Host observation can leave case-wide limitations even when the local policy's own
# structured ledger inputs were readable. These codes keep bounding the receipt, but do not veto
# absence proof for an already-recorded local finding whose original coverage was itself
# readable. ``captured_object_unavailable`` belongs here because local packs judge event
# payloads and their typed coverage, never captured-object bytes; if that absence matters to a
# rule, the pack re-fires the issue or returns its own coverage finding. Event-payload loss and
# source redaction remain excluded and therefore block.
_HOST_OBSERVATION_GAPS: Final = frozenset(
    {
        "captured_object_unavailable",
        "content_unselected",
        "host_outcome_unavailable",
        "unpaired_event",
    }
)
_BASE_DETERMINISTIC_PROOF_TOLERATED_GAPS: Final = _SEMANTIC_ONLY_GAPS | _EVIDENCE_STRENGTH_GAPS
_DETERMINISTIC_PROOF_TOLERATED_GAPS: Final = (
    _BASE_DETERMINISTIC_PROOF_TOLERATED_GAPS | _HOST_OBSERVATION_GAPS
)
_SEMANTIC_PROOF_TOLERATED_GAPS: Final = _EVIDENCE_STRENGTH_GAPS
_UNPROVEN_FRESHNESS: Final = frozenset(
    {
        LedgerFreshness.UNKNOWN,
        LedgerFreshness.REDACTED_GAP,
        LedgerFreshness.STALE_AFTER_MATERIAL_CHANGE,
    }
)
_COMMAND_GAPS: Final = frozenset({"command_attempt_uncorroborated", "command_attempt_mismatch"})
# Keep this local to avoid importing the policy module while reducers import this module. If the
# work-integrity pack changes version, its action-result exception must be reviewed explicitly.
_ACTION_WITHOUT_RESULT_POLICY: Final = ("work-integrity", "0.1.0")
ProofStateCache = MutableMapping[tuple[int, int, str], ProjectionState | None]


def _command_gap_partition(
    finding: Finding, check: CheckRecordedPayload, state: ProjectionState | None
) -> tuple[str, ...] | None:
    """Conservatively bound every possible command-gap owner at the tested frontier.

    Empty means proven independent. IDs mean overlapping command obligations; None means that
    independence is unknown. We deliberately include matching command obligations as well:
    absence of a relation on this read must never be turned into an absence proof. This narrow
    partition needs no new check payload and never reinterprets a command as execution evidence.
    """
    if (
        state is None
        or state.frontier < check.subject_frontier.sequence
        or finding.origin is not FindingOrigin.DETERMINISTIC
        or finding.kind is not FindingKind.ACTION_WITHOUT_RESULT
        or (finding.policy_id, finding.policy_version) != _ACTION_WITHOUT_RESULT_POLICY
        or finding.coverage.ledger_freshness in _UNPROVEN_FRESHNESS
        or not set(finding.coverage.known_gaps) <= _DETERMINISTIC_PROOF_TOLERATED_GAPS
        or state.coverage_gaps
        or len(finding.subject_refs) != 1
    ):
        return None
    # A check's own finding suffix does not change these inputs. A newer material row or
    # unreadable input prevents us from using current state as the historical checked state.
    for rows in (state.plans, state.obligations, state.actions, state.results, state.claims):
        if any(
            row.payload is None or row.source_frontier > check.subject_frontier.sequence
            for row in rows.values()
        ):
            return None
    scope = current_plan_scope(state.plans, state.coverage_gaps)
    if not scope.has_plan or scope.effective_obligation_refs is None:
        return None
    selected = {
        ref
        for _, row in effective_claim_items(state)
        if row.payload is not None and row.payload.claim_kind is ClaimKind.COMPLETION
        for ref in row.payload.obligation_refs
    }
    selected.update(
        key
        for key, row in state.obligations.items()
        if row.payload is not None and row.payload.status.value == "resolved"
    )
    selected.intersection_update(scope.effective_obligation_refs)
    if any(key not in state.obligations for key in selected):
        return None
    commands = {
        key
        for key in selected
        if (payload := state.obligations[key].payload) is not None
        and any(item.item_kind is RequestedItemKind.COMMAND for item in payload.requested_items)
    }
    if not commands:
        return None  # No bounded producer for the recorded command gap.
    actions = [
        row for row in state.actions.values() if row.source_event_id == finding.subject_refs[0]
    ]
    if len(actions) != 1 or (action := actions[0].payload) is None or not action.obligation_refs:
        return None
    if any(ref not in state.obligations for ref in action.obligation_refs):
        return None
    if not any(
        row.payload is not None and row.payload.action_id == action.action_id
        for row in state.results.values()
    ):
        return None
    related_through_action = set(commands.intersection(action.obligation_refs))
    # command_attempts has a second explicit relation channel: a resolved command obligation can
    # cite a result whose action is this very action. Treat that owner as overlapping too; otherwise
    # an unknown command relation could be mislabeled independent merely because the action omitted
    # the obligation from its direct refs.
    related_through_result = {
        key
        for key in commands
        if (
            (payload := state.obligations[key].payload) is not None
            and any(
                ref.startswith("res_")
                and (result := state.results.get(ResultId(ref))) is not None
                and result.payload is not None
                and result.payload.action_id == action.action_id
                for ref in payload.resolution_evidence_refs
            )
        )
    }
    return tuple(sorted(related_through_action | related_through_result))


def issue_key(finding: Finding) -> IssueKey:
    """The durable identity of the issue a finding reports.

    Two findings with the same key are the same issue at different times: the newer row
    supersedes the older one and starts unresolved.
    """

    return (
        finding.origin,
        finding.policy_id,
        finding.policy_version,
        finding.kind,
        finding.subject_refs,
    )


def _scope_covers(check: CheckRecordedPayload, finding: Finding) -> bool:
    """Whole-case checks cover every finding; scoped checks must name one of its subjects."""

    scope = check.scope
    if not scope.claim_ids and not scope.obligation_ids:
        return True
    selected = frozenset(scope.claim_ids) | frozenset(scope.obligation_ids)
    return any(ref in selected for ref in finding.subject_refs)


def _policy_completed(check: CheckRecordedPayload, finding: Finding) -> bool:
    return any(
        execution.policy_id == finding.policy_id
        and execution.policy_version == finding.policy_version
        and execution.outcome == "run"
        and execution.reason == "completed"
        for execution in check.policy_executions
    )


def _deterministic_freshness_proven(
    finding: Finding,
    check: CheckRecordedPayload,
    gaps: frozenset[str],
) -> bool:
    """Whether aggregate freshness still proves this local issue absent.

    ``redacted_gap`` is normally unproven. The only exception is a closed host-observation class
    on a check of a local finding whose own recorded proof was readable. This prevents an
    unrelated unavailable capture from making repair impossible while keeping unknown freshness,
    stale state, unreadable original proof, and every unclassified gap fail-closed.
    """

    freshness = check.coverage.ledger_freshness
    host_limited = bool(gaps & _HOST_OBSERVATION_GAPS)
    if host_limited:
        finding_coverage = finding.coverage
        finding_gaps = frozenset(finding_coverage.known_gaps)
        if (
            finding_coverage.ledger_freshness in _UNPROVEN_FRESHNESS
            or not finding_gaps <= _DETERMINISTIC_PROOF_TOLERATED_GAPS
        ):
            return False
    if freshness not in _UNPROVEN_FRESHNESS:
        return True
    return (
        freshness is LedgerFreshness.REDACTED_GAP
        and host_limited
        and gaps <= _DETERMINISTIC_PROOF_TOLERATED_GAPS
    )


def qualifying_check_resolves(
    finding: Finding,
    finding_source_frontier: int,
    check: CheckRecordedPayload,
    returned_issue_keys: frozenset[IssueKey],
    *,
    proof_state: ProjectionState | None = None,
) -> bool:
    """True when *check* proves the issue *finding* reports is absent from the state it tested.

    ``finding_source_frontier`` is the ledger sequence at which the finding was recorded; a check
    whose tested subject frontier is earlier never saw the finding, so it cannot speak to it.
    ``returned_issue_keys`` are the issue keys of every finding the check returned; a check that
    returned the same issue re-fired it rather than proving it gone.
    """

    if type(finding) is not Finding or type(check) is not CheckRecordedPayload:
        raise ValueError("finding_resolution_invalid")
    if type(finding_source_frontier) is not int or finding_source_frontier < 1:
        raise ValueError("finding_resolution_invalid")
    return not resolution_blockers(
        finding, finding_source_frontier, check, returned_issue_keys, proof_state=proof_state
    )


def resolution_blockers(
    finding: Finding,
    finding_source_frontier: int,
    check: CheckRecordedPayload,
    returned_issue_keys: frozenset[IssueKey],
    *,
    proof_state: ProjectionState | None = None,
) -> tuple[str, ...]:
    """Explain the exact qualification predicate without weakening its proof requirements."""

    reasons: list[str] = []
    if check.subject_frontier.sequence < finding_source_frontier:
        reasons.append("finding_not_in_checked_frontier")
    if issue_key(finding) in returned_issue_keys:
        reasons.append("issue_returned_again")
    if check.suppressed_count != 0:
        reasons.append("findings_suppressed")
    if not _policy_completed(check, finding):
        reasons.append("matching_policy_not_completed")
    if not _scope_covers(check, finding):
        reasons.append("subject_outside_checked_scope")
    gaps = frozenset(check.coverage.known_gaps)
    if finding.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED:
        tolerated = _SEMANTIC_PROOF_TOLERATED_GAPS
        if check.coverage.ledger_freshness in _UNPROVEN_FRESHNESS:
            reasons.append("freshness_unproven")
        if (
            check.semantic_status is not SemanticStatus.SUCCEEDED
            or check.semantic_reason is not SemanticReason.SEMANTIC_COMPLETED
        ):
            reasons.append("semantic_review_not_completed")
    else:
        tolerated = _DETERMINISTIC_PROOF_TOLERATED_GAPS
        freshness_gaps = gaps
        if gaps & _COMMAND_GAPS:
            partition = _command_gap_partition(finding, check, proof_state)
            if partition == ():
                tolerated = tolerated | _COMMAND_GAPS
                freshness_gaps = gaps - _COMMAND_GAPS
            elif partition is not None:
                reasons.append("command_relation_overlaps_obligation:" + ",".join(partition[:16]))
                if len(partition) > 16:
                    reasons.append("additional_command_obligations_omitted")
            else:
                reasons.append("command_relation_independence_unproven")
        if not _deterministic_freshness_proven(finding, check, freshness_gaps):
            reasons.append("freshness_or_original_proof_unreadable")
    reasons.extend("coverage:" + gap for gap in sorted(gaps - tolerated))
    return tuple(reasons)


def _command_partition_candidate(
    finding: Finding,
    finding_source_frontier: int,
    check: CheckRecordedPayload,
    returned_issue_keys: frozenset[IssueKey],
) -> bool:
    """Return whether replay could affect this explanation's command-gap result.

    Keep the cheap shape and applicability guards ahead of historical replay. In particular, a
    status page may contain many semantic findings that can never use the local command exception.
    """
    return (
        bool(set(check.coverage.known_gaps) & _COMMAND_GAPS)
        and finding.origin is FindingOrigin.DETERMINISTIC
        and finding.kind is FindingKind.ACTION_WITHOUT_RESULT
        and (finding.policy_id, finding.policy_version) == _ACTION_WITHOUT_RESULT_POLICY
        and finding_source_frontier <= check.subject_frontier.sequence
        and issue_key(finding) not in returned_issue_keys
        and check.suppressed_count == 0
        and _policy_completed(check, finding)
        and _scope_covers(check, finding)
        and finding.coverage.ledger_freshness not in _UNPROVEN_FRESHNESS
        and set(finding.coverage.known_gaps) <= _DETERMINISTIC_PROOF_TOLERATED_GAPS
        and len(finding.subject_refs) == 1
    )


def _historical_proof_state(
    check: CheckRecordedPayload,
    candidate: LedgerRecord,
    records: tuple[LedgerRecord, ...],
    cache: ProofStateCache,
) -> ProjectionState | None:
    """Replay the pre-check projection, caching it for findings sharing that candidate."""
    candidate_sequence = candidate.ledger.ingestion_sequence
    key = (
        candidate_sequence,
        check.subject_frontier.sequence,
        check.subject_frontier.head_digest,
    )
    if key in cache:
        return cache[key]

    # Rebuild the projection immediately before the candidate check. The shared partition then
    # applies its own frontier guards: newer plan/obligation/action/result/claim rows fail closed,
    # while an observation/evidence/finding suffix that the reducer permits remains equivalent.
    proof_state: ProjectionState | None = None
    pre_check = tuple(row for row in records if row.ledger.ingestion_sequence < candidate_sequence)
    if len(pre_check) == candidate_sequence - 1:
        from yoetz.kernel.reducers import replay

        proof_state = replay(pre_check)
    cache[key] = proof_state
    return proof_state


def _superseded_coordination_context(
    state: ProjectionState, finding: Finding
) -> tuple[int, int] | None:
    """Return ``(generation, marker frontier)`` when a revoked context closes this finding.

    A coordination finding's subject is the delivered context event it was derived from.  The
    coordination runtime records a service-stamped ``revoked`` context for the same delivery when
    that project generation is superseded (#842); this is the replay-derived fact that lets a
    receipt say *why* a later check stopped returning the finding.
    """

    if finding.kind is not FindingKind.COORDINATION_OVERLAP:
        return None
    for ref in finding.subject_refs:
        subject = state.coordination_contexts.get(EventId(ref))
        if subject is None or subject.payload is None:
            continue
        delivered = subject.payload
        identity = (
            delivered.detection_id,
            delivered.project_id,
            delivered.membership_generation,
            delivered.recipient_task_id,
            delivered.counterpart_task_id,
        )
        markers = tuple(
            record.source_frontier
            for record in state.coordination_contexts.values()
            if record.payload is not None
            and CoordinationGapCode.REVOKED in record.payload.gap_codes
            and (
                record.payload.detection_id,
                record.payload.project_id,
                record.payload.membership_generation,
                record.payload.recipient_task_id,
                record.payload.counterpart_task_id,
            )
            == identity
        )
        if markers:
            return delivered.membership_generation, min(markers)
    return None


def _check_subject_sequence(
    records: tuple[LedgerRecord, ...], check_event_id: EventId | None
) -> int | None:
    if check_event_id is None:
        return None
    for row in records:
        if row.event_id == check_event_id and isinstance(row.payload, CheckRecordedPayload):
            return row.payload.subject_frontier.sequence
    return None


def finding_resolution_explanation(
    state: ProjectionState,
    finding_id: FindingId,
    records: tuple[LedgerRecord, ...],
    *,
    proof_state_cache: ProofStateCache | None = None,
) -> str:
    """A bounded presentation derived from the latest recorded candidate, never response prose."""

    finding_record = state.findings.get(finding_id)
    if finding_record is None or finding_record.payload is None:
        return "Resolution explanation unavailable: original finding is unreadable."
    superseded = _superseded_coordination_context(state, finding_record.payload)
    if finding_is_resolved(state, finding_id):
        resolving = finding_record.resolved_by_check_event_id
        resolving_sequence = _check_subject_sequence(records, resolving)
        if (
            superseded is not None
            and resolving_sequence is not None
            and superseded[1] <= resolving_sequence
        ):
            return (
                f"Resolved by qualifying check {resolving} after project coordination generation "
                f"{superseded[0]} was superseded; the context is retained as history, not as a "
                "current coordination obligation."
            )
        return f"Resolved by qualifying check {resolving}; retained as history."
    candidate = next(
        (
            row
            for row in reversed(records)
            if row.schema.name == "check_recorded"
            and finding_record.source_frontier < row.ledger.ingestion_sequence <= state.frontier
        ),
        None,
    )
    if superseded is not None:
        candidate_sequence = (
            None if candidate is None else _check_subject_sequence(records, candidate.event_id)
        )
        if candidate_sequence is None or candidate_sequence < superseded[1]:
            return (
                f"Unresolved: project coordination generation {superseded[0]} was superseded, so "
                "this is historical context rather than a current coordination obligation; the "
                "later qualifying coordination check can resolve it."
            )
    if candidate is None:
        return "Unresolved: no later recorded check is available for an absence proof."
    check = candidate.payload
    if (
        not isinstance(check, CheckRecordedPayload)
        or f"redacted_event:{candidate.event_id}" in state.coverage_gaps
    ):
        return f"Unresolved: check {candidate.event_id} is unreadable; absence is unproven."
    returned = [state.findings.get(key) for key in check.returned_finding_ids]
    if any(row is None or row.payload is None for row in returned):
        return f"Unresolved: returned findings of check {candidate.event_id} are unreadable."
    keys = frozenset(
        issue_key(row.payload) for row in returned if row is not None and row.payload is not None
    )
    proof_state = None
    if _command_partition_candidate(
        finding_record.payload, finding_record.source_frontier, check, keys
    ):
        proof_state = _historical_proof_state(
            check,
            candidate,
            records,
            {} if proof_state_cache is None else proof_state_cache,
        )
    reasons = resolution_blockers(
        finding_record.payload, finding_record.source_frontier, check, keys, proof_state=proof_state
    )
    returned_again = "issue_returned_again" in reasons
    relation = "Returned again" if returned_again else "Not returned; absence remains unproven"
    if not reasons:
        # An unavailable or provenance-disputed response can retain the public resolved=false pin.
        reasons = ("response_unavailable_or_provenance_disputed",)
    detail = ", ".join(reasons)
    if len(detail.encode("utf-8")) > 5000:
        detail = (
            detail.encode("utf-8")[:4900].decode("utf-8", errors="ignore")
            + "... (additional requirements omitted; inspect recorded check coverage)"
        )
    return (
        f"{relation} in check {candidate.event_id} of subject frontier "
        f"{check.subject_frontier.sequence}. Resolution requirements not met: {detail}. "
        "Acknowledgement is not repair evidence; an unchanged recheck cannot remove durable proof limits."
    )


def append_resolution_explanation(detail: str, explanation: str) -> str:
    """Keep the existing content field's UTF-8 bound and visibly mark any shortened original."""

    suffix = "\n\nResolution: " + explanation
    budget = 8192 - len(suffix.encode("utf-8"))
    if len(detail.encode("utf-8")) > budget:
        detail = (
            detail.encode("utf-8")[: max(0, budget - 3)].decode("utf-8", errors="ignore") + "..."
        )
    return detail + suffix


def apply_check_resolution(
    findings: dict[FindingId, FindingProjectionRecord],
    check: CheckRecordedPayload,
    check_event_id: EventId,
    *,
    proof_state: ProjectionState | None = None,
) -> None:
    """Fold one recorded check into the resolution facts of the findings it could speak to.

    Every finding the check returned becomes current again, whatever an earlier check proved.
    If any returned finding is unreadable, the check cannot prove which issues it re-fired, so it
    resolves nothing. Otherwise each readable, still-current finding that the qualification
    relation admits is marked resolved by this check.
    """

    returned_keys: set[IssueKey] = set()
    readable = True
    for returned_id in check.returned_finding_ids:
        record = findings.get(returned_id)
        if record is None or record.payload is None:
            readable = False
            continue
        returned_keys.add(issue_key(record.payload))
        if record.resolved_by_check_event_id is not None:
            findings[returned_id] = replace(record, resolved_by_check_event_id=None)
    if not readable:
        return
    frozen_keys = frozenset(returned_keys)
    for current_id, record in tuple(findings.items()):
        if (
            record.payload is None
            or record.resolved_by_check_event_id is not None
            or current_id in check.returned_finding_ids
        ):
            continue
        if qualifying_check_resolves(
            record.payload, record.source_frontier, check, frozen_keys, proof_state=proof_state
        ):
            findings[current_id] = replace(record, resolved_by_check_event_id=check_event_id)


def reopen_findings_resolved_by(
    findings: dict[FindingId, FindingProjectionRecord],
    event_ids: frozenset[EventId],
) -> None:
    """Drop resolution whose proving check was redacted: unreadable proof is no proof."""

    for current_id, record in tuple(findings.items()):
        if record.resolved_by_check_event_id in event_ids:
            findings[current_id] = replace(record, resolved_by_check_event_id=None)


def finding_is_resolved(state: ProjectionState, finding_id: FindingId) -> bool:
    """The one shared answer every surface reads for ``resolved``.

    True only when a later qualifying check proved the issue absent *and* the finding's latest
    response, if any, is readable and is not ``provenance_disputed``. The released
    ``status-result`` wire pins ``provenance_disputed`` rows to ``resolved=false``; that pin is
    honoured here, conservatively, rather than letting the receipt and status disagree.
    """

    record = state.findings.get(finding_id)
    if record is None or record.payload is None or record.resolved_by_check_event_id is None:
        return False
    response = state.responses.get(finding_id)
    if response is None:
        return True
    if response.payload is None:
        return False
    return response.payload.disposition is not ResponseDisposition.PROVENANCE_DISPUTED


def resolved_finding_ids(state: ProjectionState) -> frozenset[FindingId]:
    """Every finding id the shared rule reports as resolved."""

    return frozenset(key for key in state.findings if finding_is_resolved(state, key))
