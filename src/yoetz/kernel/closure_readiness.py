"""Closed classification of coverage gap codes for closure readiness (ADR-031).

Closure readiness answers one question for the agent: is there anything left that *it* can do, or
is every remaining condition a limitation that the receipt will disclose? A gap code answers that
only through this table. Each code the product can emit is assigned exactly once:

* ``agent_actionable`` — a documented agent action (publish, respond, revise a claim or plan,
  recheck after a material change, or re-read status once the service catches up) removes it.
* ``standing_limitation`` — no agent action available under the current host, profile and privacy
  policy removes it. It describes the bounds of observation, capture, review or the ledger and is
  disclosed on the receipt; it is never an instruction.
* ``route_dependent`` — only ``semantic_review_not_requested``: standing on a route where
  AI-powered review is optional or off, and actionable only on a route that requires it when no
  AI-powered review has completed since the last material change (remedy: run it).

The table is a closed, versioned vocabulary rather than a heuristic. A conformance test
(``tests/conformance/honesty/test_gap_classification_completeness.py``) enumerates every gap-code
producer in the codebase and fails when one emits a code this table does not classify, so a new
code cannot ship unclassified. A code the running build does not know (for example one read from a
ledger written by a newer build) is reported as ``unclassified_gap:<code>`` in the agent-actionable
group: the conservative default stays visible instead of silently masking a missing entry.

Classification never removes, renames or weakens a gap. Coverage ``known_gaps``, receipts and
verdicts are unchanged; this module only decides which readiness group names the code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

__all__ = [
    "GAP_CLASSIFICATION",
    "GAP_CLASSIFICATION_VERSION",
    "UNCLASSIFIED_GAP_PREFIX",
    "GapClass",
    "GapSplit",
    "classify_gap",
    "gap_base_code",
    "split_gaps",
]

# Bump when any assignment below changes meaning. Status responses carry the version so a reader
# can tell which table classified a recorded readiness answer.
GAP_CLASSIFICATION_VERSION: Final = "1"
UNCLASSIFIED_GAP_PREFIX: Final = "unclassified_gap:"

_CODE_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
# Markers that wrap a base code in a named envelope: ``coverage:<code>`` (finding resolution),
# ``check_coverage:<code>``, ``retained_finding_coverage:<code>``, ``semantic_outcome:<code>``
# (receipt case gaps) and ``lineage:<code>:<child>:<event>`` (receipt lineage gaps). Every other
# ``<code>:<subject...>`` marker (``unknown_event:``, ``missing_ref:``, ``redacted_event:``,
# ``completion_scope_undeclared:<event>``) already leads with its base code.
_WRAPPER_PREFIXES: Final = frozenset(
    {"check_coverage", "coverage", "lineage", "retained_finding_coverage", "semantic_outcome"}
)


class GapClass(str, Enum):  # noqa: UP042 - exact internal contract token
    AGENT_ACTIONABLE = "agent_actionable"
    STANDING_LIMITATION = "standing_limitation"
    ROUTE_DEPENDENT = "route_dependent"


_A: Final = GapClass.AGENT_ACTIONABLE
_S: Final = GapClass.STANDING_LIMITATION
_R: Final = GapClass.ROUTE_DEPENDENT

GAP_CLASSIFICATION: Final[Mapping[str, GapClass]] = MappingProxyType(
    {
        # -- Host observation (ObservationGapCode and the local observation store). Recorded into
        # the coverage of the observed rows; host-profile, consent, capture-profile and delivery
        # facts that no agent action rewrites.
        "unpaired_event": _S,  # host pairing loss; sticky by design (#917 owns its surfacing)
        "unsupported_event": _S,
        "unsupported_format": _S,
        "truncated_payload": _S,  # bounded capture or packet truncation (disclosure, #904)
        "service_unavailable": _S,
        "vault_locked": _S,
        "ledger_rejected": _S,
        "dedup_conflict": _S,
        "cursor_stale": _S,
        "consent_missing": _S,
        "consent_revoked": _S,
        "source_lag": _S,
        "mapping_missing": _S,
        "missing_subagent_identity": _S,
        "session_superseded": _S,
        "outbox_overflow": _S,
        "observation_input_loss": _S,
        "outbox_quarantined": _S,
        "observation_storage_corrupt": _S,
        "quarantine_detail_evicted": _S,
        "content_capture_unavailable": _S,  # consent or capture profile
        "capture_budget_exhausted": _S,
        "content_capture_profile_mismatch": _S,
        "content_unselected": _S,  # selection policy chose not to capture
        "content_redacted": _S,  # privacy-policy redaction
        "routine_read_detail_omitted": _S,
        "routine_read_summary_invalid": _S,
        "routine_summary_invalid": _S,
        "payload_too_large": _S,
        "payload_content_omitted": _S,
        "selection_route_changed": _S,
        "policy_untrusted": _S,
        "verification_stale": _S,
        "network_check_unsupported": _S,
        "pending_attempt_expired": _S,  # open pre-events pruned by TTL or generation fence
        "pending_attempt_limit": _S,
        "optional_observation_detail_omitted": _S,
        "drain_diagnostic_unavailable": _S,
        "host_outcome_unavailable": _S,  # the host did not state an outcome (#910)
        # -- Background observation advice: pipeline state, not an agent task (#923).
        "advice_coverage_gaps_truncated": _S,
        "advice_evidence_refs_truncated": _S,
        "advice_ranked_findings_truncated": _S,
        "advice_semantic_deferred": _S,
        "advice_semantic_output_invalid": _S,
        "advice_semantic_pending": _S,
        "advice_semantic_text_truncated": _S,
        "advice_semantic_unavailable": _S,
        "observation_qualified_partial": _S,
        # -- Completion scope (ADR-019). A scope difference is repaired by the agent: claim the
        # omitted plan item, revise the plan or claim, declare obligations, or acknowledge the
        # obligation as not done. An explicit "no obligations" declaration is the agent's own
        # recorded choice and stays a disclosure.
        "completion_plan_not_claimed": _A,
        "completion_claim_outside_plan": _A,
        "completion_scope_undeclared": _A,
        "completion_scope_declared_none": _S,
        # -- Check applicability. A missing or superseded check is removed by running a check
        # after the material change; a check that only trails attributable answers and
        # observations is a disclosure (real material staleness reads check_not_applicable).
        "check_not_recorded": _A,
        "check_not_applicable": _A,
        "check_current_as_of_earlier_frontier": _S,
        "check_payload_unavailable": _S,
        # -- AI-powered review outcome and packet bounds: disclosure of how the review was bounded.
        "semantic_review_not_requested": _R,
        "semantic_review_not_configured": _S,
        "semantic_relevance_review_not_run": _S,  # provider or evaluator failure
        "semantic_review_context_withheld": _S,
        "semantic_packet_insufficient": _S,  # agent-suppliable items arrive as findings (#907)
        "semantic_challenges_rejected": _S,  # reviewer output dropped by the validation fence
        "semantic_case_content_over_item_limit": _S,  # packet item bound (#907)
        "semantic_case_finding_refs_over_limit": _S,
        "semantic_case_capacity_exceeded": _S,
        "semantic_reference_scope_reduced": _S,  # bounded selection (#904)
        "optional_semantic_review_blocked_by_policy": _S,
        "optional_semantic_review_registration_drift": _S,  # owner reinstall, not the agent
        # -- Ledger and evidence facts found by the deterministic case.
        "unknown_event": _S,  # written by a newer build
        "redacted_event": _S,
        "redacted_object": _S,
        "event_payload_unavailable": _S,
        "captured_object_unavailable": _S,
        "missing_ref": _A,  # publish the referenced record or supersede the reference
        "evidence_content_digest_only": _S,  # provenance label (#912)
        "evidence_content_withheld": _S,
        "evidence_digest_subject_legacy_unknown": _S,
        "unknown_event_schema_preserved": _S,
        # -- Command attempts: an uncorroborated command has no observable command identity
        # (#909); a mismatch is removed by running the requested command or correcting the
        # assertion.
        "command_attempt_uncorroborated": _S,
        "command_attempt_mismatch": _A,
        # -- Lineage (ADR-027). Child work, child verification and a check that has not covered
        # the latest manifest are actionable; unreadable, restricted or terminal child facts are
        # disclosures. A manifest that trails the catalog is refreshed by the service, so, like
        # ``projection_stale``, the remedy is to re-read status rather than a standing limit.
        "lineage_child_actionable_finding": _A,
        "lineage_child_open": _A,
        "lineage_child_incomplete": _A,
        "lineage_child_check_stale": _A,
        "lineage_child_verification_unknown": _A,
        "lineage_child_check_frontier_unknown": _S,
        "lineage_child_coverage_gap": _S,
        "lineage_child_frontier_unknown": _S,
        "lineage_child_provenance_restricted": _S,
        "lineage_child_read_gap": _S,
        "lineage_child_unavailable": _S,
        # Always accompanied by lineage_child_actionable_finding, which carries the action.
        "lineage_invalid_acceptance_transition": _S,
        "lineage_manifest_uncovered": _A,
        "lineage_manifest_not_recorded": _A,
        "lineage_manifest_state_changed": _A,
        "lineage_readiness_unavailable": _A,
        "lineage_manifest_missing": _S,
        "lineage_manifest_not_authorized": _S,
        "lineage_manifest_quarantined": _S,
        "lineage_manifest_revoked": _S,
        "lineage_manifest_unknown": _S,
        "lineage_manifest_unreadable": _S,
        # -- Project and coordination detail bounds.
        "project_member_unavailable": _S,
        "source_unavailable": _S,
        "revoked": _S,
        "not_observable": _S,
        "details_truncated": _S,
        # -- Imported source streams (Codex JSONL and rollout imports, human imports). The import
        # records what the source stream held; the agent cannot rewrite the imported source.
        "import_source_range_not_universal": _S,
        "human_import_scope_not_universal": _S,
        "command_declined_not_executed": _S,
        "command_outcome_incomplete": _S,
        "file_content_not_captured": _S,
        "final_newline_absent": _S,
        "item_shape_unsupported": _S,
        "item_transition_invalid": _S,
        "json_profile_unsupported": _S,
        "line_oversized": _S,
        "malformed_line": _S,
        "result_outcome_incomplete": _S,
        "source_category_not_mapped": _S,
        "source_line_unmapped": _S,
        "source_text_not_represented": _S,
        "source_timestamp_unavailable": _S,
        "truncated_final_line": _S,
        "unknown_item_type": _S,
        "unknown_wrapper_type": _S,
        "unsupported_codex_profile": _S,
        "web_results_not_captured": _S,
        "wrapper_shape_unsupported": _S,
    }
)
if any(_CODE_RE.fullmatch(code) is None for code in GAP_CLASSIFICATION):  # pragma: no cover
    raise ValueError("gap_classification_code_invalid")
if [code for code, value in GAP_CLASSIFICATION.items() if value is _R] != [
    "semantic_review_not_requested"
]:  # pragma: no cover - the route rule is defined for exactly one code
    raise ValueError("gap_classification_route_rule_invalid")


def gap_base_code(marker: str) -> str:
    """Return the base code a gap marker or prefixed form classifies by.

    ``coverage:<code>``, ``check_coverage:<code>``, ``retained_finding_coverage:<code>``,
    ``semantic_outcome:<code>`` and ``lineage:<code>:…`` name their base code second; every other
    ``<code>:<subject…>`` marker names it first. A bare code is its own base.
    """

    if type(marker) is not str:
        raise TypeError("gap_marker_invalid")
    head, separator, rest = marker.partition(":")
    if not separator:
        return marker
    if head in _WRAPPER_PREFIXES:
        return rest.partition(":")[0]
    return head


def classify_gap(
    marker: str,
    *,
    semantic_review_required: bool,
    semantic_review_current: bool,
) -> GapClass | None:
    """Resolve one gap to ``agent_actionable`` or ``standing_limitation``; ``None`` if unknown.

    ``semantic_review_required`` is true only on a route whose verification policy requires
    AI-powered review. ``semantic_review_current`` is true when an AI-powered review completed
    and no material change has been recorded since. Neither flag affects any other code.
    """

    assigned = GAP_CLASSIFICATION.get(gap_base_code(marker))
    if assigned is not _R:
        return assigned
    if semantic_review_required and not semantic_review_current:
        return _A
    return _S


@dataclass(frozen=True, slots=True)
class GapSplit:
    """Gap codes grouped for closure readiness; each tuple is sorted and duplicate-free."""

    agent_actionable: tuple[str, ...]
    standing_limitations: tuple[str, ...]


def _unclassified(code: str) -> str:
    # Page gaps are already validated tokens; bound anything else to a fixed, content-free token
    # so an unreadable value can never echo into a structural readiness field.
    return UNCLASSIFIED_GAP_PREFIX + (code if _CODE_RE.fullmatch(code) else "unreadable_gap_code")


def split_gaps(
    markers: Iterable[str],
    *,
    semantic_review_required: bool,
    semantic_review_current: bool,
) -> GapSplit:
    """Split gap markers into agent-actionable and standing groups by their base codes.

    An unknown base code lands in the agent-actionable group as ``unclassified_gap:<code>``.
    """

    actionable: set[str] = set()
    standing: set[str] = set()
    for marker in markers:
        code = gap_base_code(marker)
        assigned = classify_gap(
            code,
            semantic_review_required=semantic_review_required,
            semantic_review_current=semantic_review_current,
        )
        if assigned is None:
            actionable.add(_unclassified(code))
        elif assigned is _A:
            actionable.add(code)
        else:
            standing.add(code)
    return GapSplit(
        tuple(sorted(actionable, key=str.encode)),
        tuple(sorted(standing, key=str.encode)),
    )
