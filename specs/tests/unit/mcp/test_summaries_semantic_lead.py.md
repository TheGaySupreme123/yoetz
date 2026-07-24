# tests/unit/mcp/test_summaries_semantic_lead.py — deterministic-only summary prominence suite

**Wave:** C | **ADRs:** ADR-006 | **Imports (spec-tree):** `src/yoetz/mcp/summaries.md`,
`src/yoetz/application/check.md` | **Imported by:** test runner

## Purpose

Freeze that a deterministic-only check announces its own limitation first. The compact summary is
the only check text most agents read, and when it led with a clean verdict and buried the semantic
status, a real agent read `no_issue_detected` as an endorsement of work no semantic reviewer had
ever looked at.

## Public surface

Assertions over `summary_for_check` for a deterministic-only envelope.

## Behavior

When semantic status is `not_requested`, the summary opens with the semantic-review-not-requested
statement and only then reports the deterministic verdict, findings, suppressed count, and frontier.
Summaries for envelopes whose semantic review did run keep the verdict-first ordering.

## Errors and edge cases

The rendered summary stays inside the bounded ASCII budget with the added lead text.

## Invariants

1. A deterministic-only summary can never be read as a completed implementation review.
2. The summary reports only fields present in the envelope; no field is inferred.

## Tests

This file is the executable owner. The coverage gap that accompanies the same condition is owned by
`tests/unit/application/test_verdict_rules.py`.

## Open questions

None.
