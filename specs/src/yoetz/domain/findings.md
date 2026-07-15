# src/yoetz/domain/findings.py — canonical finding values and ranking inputs

**Wave:** B | **ADRs:** ADR-002, ADR-006 | **Imports (spec-tree):** `protocol/coverage.md`,
`protocol/errors.md`, `domain/values.md`
**Imported by:** `kernel/ranking.md`, `kernel/deterministic_checks.md`, `domain/receipts.md`,
`application/check.md`, `application/respond.md`, `cli/render.md`

## Purpose

Findings are the user-visible output of Yoetz’s checking loop. This file defines the immutable
finding values that deterministic policies, semantic evaluation, and receipts all agree on. It is
the place where findings remain sparse, ranked, and coverage-bound instead of turning into free-form
assistant prose.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `FindingKind` | enum of deterministic and semantic finding kinds from `specs/INTERFACES.md` |
| `CheckVerdict` | enum: `action_required`, `no_issue_detected`, `insufficient_coverage`, `incomplete_check` |
| `CandidateFinding` | frozen ID-free value produced by pure deterministic/semantic post-validation |
| `Finding` | frozen dataclass `(finding_id, kind, origin, priority, summary, detail, subject_refs, policy_id, policy_version, subject_frontier, coverage, provenance)` |
| `DeterministicFinding` | alias of `Finding` for deterministic-origin findings |
| `SemanticFinding` | alias of `Finding` for semantic-origin findings |
| `RankedFindings` | frozen wrapper for ordered findings and suppressed-count metadata |
| `SemanticProvenance` | frozen provenance record for semantic findings |
| `rank_key(finding)` | deterministic sort key helper |

## Behavior

`CandidateFinding` has every logical `Finding` field except `finding_id`. Pure policy functions
return candidates so they remain deterministic and cannot read ambient randomness. The application
normalizes candidates, allocates one OS-CSPRNG `fnd_` ID for each, persists that map in the durable
local-result object, and constructs immutable `Finding` values. A crash/retry reopens the map and
never allocates replacement IDs.

`Finding` is a frozen value object. It must never depend on mutable provider SDK output, logging
context, or database rows after construction.

The `origin` field distinguishes deterministic policy findings from semantic-model-derived
findings. `FindingKind` describes the issue and never implies origin: the deterministic
research-evidence pack and semantic reviewer may both produce an evidence-assessment kind. The same
surface type is used for both because the CLI, MCP, and receipt layers need one common
representation, but origin/provenance and policy fields record which path produced it.

For a post-validated semantic `ReviewerChallenge`, `summary` states the discrepancy and `detail`
contains the bounded direct message to the main agent, alternative interpretation, uncertainty,
and smallest requested next step. This uses the existing finding schema so the message passes the
existing `finding_summary` agent-context privacy fence. It does not turn a finding into a chat
transcript or grant the model response/waiver authority.

The challenge's internal `cited_refs` are not copied blindly. Post-validation resolves a cited
action/result/evidence/frontier-finding to its recorded event/obligation/claim roots and resolves a
same-check deterministic finding ID to that candidate's already frozen `subject_refs`. The semantic
finding stores the sorted unique root union only. Resolution outside the frozen ref graph, an empty
root union, or a local same-check finding that was not durably pinned rejects the challenge. This
keeps public `subject_refs` valid even when the cited deterministic finding is later suppressed by
the result cap.

`priority` uses the shared three-level scheme:

- `1` = highest material priority, user action likely required;
- `2` = important but not first;
- `3` = lower-priority or explanatory material.

`coverage` is the finding’s weakest material coverage. It must be conservative: imported or stale
material weakens it, and semantic output can never strengthen it past the evidence it actually saw.

`subject_refs` is the stable tuple of event, obligation, or claim IDs that justify the finding.
It is bounded, ordered, and canonicalized. It is never allowed to contain raw free text.

`SemanticProvenance` captures the minimum finalized audit trail for a semantic finding: provider
profile, model and attempt identity, dispatch kind, external authorization or local-disclosure
reservation, durable privacy receipt, request commitment when external, exact semantic
status/reason, bounded usage, and failure class if any. The provenance record is part of the value,
not an external log lookup. A provider adapter's provisional `ProviderAttemptProvenance` is never
valid here; the coordinator may construct this value only after the matching privacy receipt is
durable.

`RankedFindings` preserves:

- the ordered list returned to the caller;
- the number of suppressed lower-priority findings;
- the final verdict implied by the set.

`rank_key(finding)` returns a deterministic ordering tuple that sorts by materiality, actionability,
evidence strength, and then finding ID as the final tie-break. That tie-break is the only reason two
equal-priority findings render stably across runs.

## Errors and edge cases

- Unknown finding kinds are invalid at the boundary.
- A semantic finding without finalized provenance is invalid. An imported semantic observation
  preserves its original finalized provenance or remains an opaque/import-gap observation; it may
  not fabricate a current semantic finding.
- Findings never expose more than three items by default at the CLI surface, even if more are stored.
- `CheckVerdict` never has a value named `pass`.
- A finding cannot claim stronger coverage than its subject frontier or supporting refs justify.

## Invariants

1. Findings are sparse and ordered deterministically.
2. Coverage is always explicit.
3. Prose in `summary` and `detail` is no stronger than the underlying evidence.
4. The same finding ID never changes meaning across retry.
5. Semantic provenance is auditable but bounded.
6. Pure kernel functions create candidates; only the injected `IdPort` creates finding IDs.
7. No semantic finding can precede its durable privacy receipt.
8. Finding kind and origin remain independent, and semantic challenge prose stays bounded by the
   exact supplied case.

## Tests

- `tests/unit/domain/test_findings.py` — kind, priority, and provenance validation.
- `tests/unit/kernel/test_ranking.py` — ordering and suppression-count behavior.
- `tests/subprocess/test_cli_invocations.py` — three-item cap, stable ordering, suppressed count, and
  no-stronger-than-evidence human wording.

## Open questions

None.

A later import preserves the original semantic provenance and adds imported publication/
artifact-observation coverage; it never relabels model-derived judgment as deterministic.
