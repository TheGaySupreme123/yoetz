"""Proof-based finding resolution: which later check may resolve which recorded finding.

A finding is a historical fact and stays visible forever. Whether it is *current* is a separate
fact, and only one kind of evidence may change it: a later deterministic check whose recorded
state contains the finding, whose matching policy pack ran to completion with nothing suppressed,
whose scope covers the finding's subject, whose coverage carries no weakening gap for the
finding's proof class, and which did not return the same issue again. A closed deterministic-only
exception lets case-wide host-observation limitations remain on the receipt without vetoing clean
structured-ledger proof. A response disposition never resolves a finding; it only answers it on
the record. Weak, skipped, failed, capped, stale, unreadable, or non-overlapping checks do nothing,
and nothing here ever strengthens coverage.

Everything in this module is pure and replay-derived, so a receipt, a status counter, and a
projection checkpoint all read the same fact.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from yoetz.domain.events import CheckRecordedPayload, LedgerRecord
from yoetz.domain.findings import Finding, FindingOrigin, ResponseDisposition
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
from yoetz.domain.values import EventId, FindingId
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

# Coverage gaps that describe only the semantic review's own absence or weakness. A
# deterministic finding is proven absent by the deterministic pack that owns it, so these gaps
# do not weaken that proof; for a semantic finding they do, because the semantic review is the
# proof. The registration-drift gap is one of these: it rides alongside the ceiling gap on a
# strict check served while the applied record says policy, so a drift check still resolves
# deterministic findings exactly like a plain ceiling check does (issue #537).
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
# new gap code blocks resolution until someone decides otherwise here.
_EVIDENCE_STRENGTH_GAPS: Final = frozenset(
    {
        "evidence_content_digest_only",
        "evidence_content_withheld",
        "evidence_digest_subject_legacy_unknown",
    }
)
# Host observation can leave case-wide limitations even when the deterministic policy's own
# structured ledger inputs were readable. These codes keep bounding the receipt, but do not veto
# absence proof for an already-recorded deterministic finding whose original coverage was itself
# readable. ``captured_object_unavailable`` belongs here because deterministic packs judge event
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
    """Whether aggregate freshness still proves this deterministic issue absent.

    ``redacted_gap`` is normally unproven. The only exception is a closed host-observation class
    on a check of a deterministic finding whose own recorded proof was readable. This prevents an
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
    return not resolution_blockers(finding, finding_source_frontier, check, returned_issue_keys)


def resolution_blockers(
    finding: Finding,
    finding_source_frontier: int,
    check: CheckRecordedPayload,
    returned_issue_keys: frozenset[IssueKey],
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
        if not _deterministic_freshness_proven(finding, check, gaps):
            reasons.append("freshness_or_original_proof_unreadable")
    reasons.extend("coverage:" + gap for gap in sorted(gaps - tolerated))
    return tuple(reasons)


def finding_resolution_explanation(
    state: ProjectionState, finding_id: FindingId, records: tuple[LedgerRecord, ...]
) -> str:
    """A bounded presentation derived from the latest recorded candidate, never response prose."""

    finding_record = state.findings.get(finding_id)
    if finding_record is None or finding_record.payload is None:
        return "Resolution explanation unavailable: original finding is unreadable."
    if finding_is_resolved(state, finding_id):
        return f"Resolved by qualifying check {finding_record.resolved_by_check_event_id}; retained as history."
    candidate = next(
        (
            row
            for row in reversed(records)
            if row.schema.name == "check_recorded"
            and finding_record.source_frontier < row.ledger.ingestion_sequence <= state.frontier
        ),
        None,
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
    reasons = resolution_blockers(
        finding_record.payload, finding_record.source_frontier, check, keys
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
        if qualifying_check_resolves(record.payload, record.source_frontier, check, frozen_keys):
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
