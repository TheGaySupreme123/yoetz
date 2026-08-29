"""Proof-based finding resolution: which later check may resolve which recorded finding.

A finding is a historical fact and stays visible forever. Whether it is *current* is a separate
fact, and only one kind of evidence may change it: a later deterministic check whose recorded
state contains the finding, whose matching policy pack ran to completion with nothing suppressed,
whose scope covers the finding's subject, whose coverage carries no weakening gap for the
finding's proof class, and which did not return the same issue again. A response disposition
never resolves a finding; it only answers it on the record. Weak, skipped, failed, capped, stale,
unreadable, or non-overlapping checks do nothing, and nothing here ever strengthens coverage.

Everything in this module is pure and replay-derived, so a receipt, a status counter, and a
projection checkpoint all read the same fact.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Final

from yoetz.domain.events import CheckRecordedPayload
from yoetz.domain.findings import Finding, FindingOrigin, ResponseDisposition
from yoetz.domain.receipts import (
    OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
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
# proof.
_SEMANTIC_ONLY_GAPS: Final = frozenset(
    {
        SEMANTIC_REVIEW_NOT_REQUESTED_GAP,
        SEMANTIC_REVIEW_NOT_CONFIGURED_GAP,
        SEMANTIC_RELEVANCE_REVIEW_NOT_RUN_GAP,
        OPTIONAL_SEMANTIC_REVIEW_BLOCKED_BY_POLICY_GAP,
        SEMANTIC_REVIEW_CONTEXT_WITHHELD_GAP,
        SEMANTIC_CHALLENGES_REJECTED_GAP,
        SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP,
    }
)
# Evidence-strength gaps: the cited evidence was readable but its content was not captured or
# was withheld, or its digest subject predates typed bindings. They bound how strong a receipt
# can be — the receipt keeps reporting them — but they do not stop a policy pack from reading
# the ledger state it judges, so they do not weaken the proof that an issue no longer fires.
# Every other gap (redacted or unavailable payloads and objects, missing refs, unknown events,
# completion-scope gaps, import ranges, and any code not named here) means the check could not
# read or bound the material, and blocks both proof classes. The set is closed on purpose: a new
# gap code blocks resolution until someone decides otherwise here.
_EVIDENCE_STRENGTH_GAPS: Final = frozenset(
    {
        "evidence_content_digest_only",
        "evidence_content_withheld",
        "evidence_digest_subject_legacy_unknown",
    }
)
_DETERMINISTIC_PROOF_TOLERATED_GAPS: Final = _SEMANTIC_ONLY_GAPS | _EVIDENCE_STRENGTH_GAPS
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
    if check.subject_frontier.sequence < finding_source_frontier:
        return False
    if issue_key(finding) in returned_issue_keys:
        return False
    if check.suppressed_count != 0:
        return False
    if not _policy_completed(check, finding):
        return False
    if not _scope_covers(check, finding):
        return False
    coverage = check.coverage
    if coverage.ledger_freshness in _UNPROVEN_FRESHNESS:
        return False
    gaps = frozenset(coverage.known_gaps)
    if finding.origin is FindingOrigin.SEMANTIC_MODEL_DERIVED:
        # The semantic review is the proof: it must have completed, and nothing may have
        # weakened what it reviewed.
        if (
            check.semantic_status is not SemanticStatus.SUCCEEDED
            or check.semantic_reason is not SemanticReason.SEMANTIC_COMPLETED
        ):
            return False
        return gaps <= _SEMANTIC_PROOF_TOLERATED_GAPS
    return gaps <= _DETERMINISTIC_PROOF_TOLERATED_GAPS


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
