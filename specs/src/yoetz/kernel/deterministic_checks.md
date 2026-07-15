# src/yoetz/kernel/deterministic_checks.py — deterministic work-policy evaluation

**Wave:** B | **ADRs:** ADR-001, ADR-002, ADR-003, ADR-004, ADR-006 | **Imports (spec-tree):**
`kernel/projections.md`, `kernel/policies/work_integrity.md`,
`kernel/policies/research_evidence.md`, `domain/findings.md`, `protocol/coverage.md`,
`protocol/errors.md`, `version.md` | **Imported by:** `application/check.md`,
`adapters/sqlite/repository.md`, `kernel/ranking.md`

## Purpose

This file contains the deterministic policy engine. It is the non-LLM part of Yoetz’s checking
loop and the first line of defense against bad work claims. It only looks at the frozen case and
the versioned policy packs. It never looks at provider output, network state, or SQLite rows.

The engine exists so the system can explain failures even when semantic evaluation is unavailable,
delayed, or refused. Deterministic checks are not a fallback in the weak sense; they are a primary
trust layer.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `run_deterministic_policies(case, policy)` | evaluate one frozen case against one policy pack |

## Behavior

`run_deterministic_policies(case, policy)` is pure. It inspects the frozen projection snapshot
contained in `case`, applies the rule set bundled in `policy`, and returns a tuple of deterministic
`CandidateFinding` values. It does not rank the findings, allocate IDs, build a receipt, or decide whether semantic
evaluation should run next.

The `policy` argument is the loaded immutable pack from `kernel/policies/*`, not a dynamic rule
source. The engine rejects an unknown or tampered policy pack rather than trying to approximate it.

The engine uses the current projection state to derive findings for the active pack:

- the work-integrity pack looks for open obligations, unattempted requested items, missing action →
  result links, stale evidence, unresolved contradictions, stale/incomplete ledgers, and hollow
  responses;
- the research-evidence pack looks for claim-evidence mismatch, diff/account mismatch, omitted
  limitations, and unjustified rejection of evidence-based findings.

Each pack runs in a fixed internal order and each rule yields zero or one finding. If two rules
describe the same logical subject, the pack keeps the strongest single finding and drops the rest
before the engine returns the tuple.

Every produced candidate must:

- carry the exact `kind`, `priority`, `summary`, `detail`, `subject_refs`,
  `policy_id`, `policy_version`, `subject_frontier`, and `coverage` required by the shared finding
  model;
- be conservative about coverage and never claim stronger support than the supporting refs justify;
- use `provenance = None` because the origin is deterministic, not semantic-model-derived.

The deterministic engine is monotonic with respect to the checked state. If the input case is more
complete, the engine may produce more findings or weaker coverage, but it must never silently
invent a stronger conclusion from weaker evidence.

The rule evaluation order is stable and pack-local. Within a pack, rules run in the fixed order
their module defines, and each rule either yields zero or one finding. Duplicate logical findings
are collapsed by the pack’s own normalization before the result is returned.

The packs themselves are rule books, not probabilistic scorers:

- `work_integrity_findings(case)` depends only on the frozen projection, frontier, allowed IDs, and
  policy version. It does not inspect provider output or semantic responses.
- `research_evidence_findings(case)` depends only on the frozen projection, frontier, allowed IDs,
  and policy version. It does not inspect live network content or the raw user prompt.

The engine keeps the pack results separate so that a caller can see whether a failure comes from
work integrity, research evidence, or both. The separation matters because a project can have clean
work integrity and still have weak evidence, or vice versa.

Rule-level expectations are:

- open obligations and unattempted requested items produce findings only when the case shows they
  were actually required and remain unresolved;
- stale evidence produces findings only when the evidence refers to a materially different state
  than the one being checked;
- a claim/evidence mismatch produces a finding only when the subject refs really fail to support
  the statement, not merely when the statement is short;
- a rejected or waived finding becomes a finding only when the waiver or rejection lacks a credible
  basis in the frozen record.

## Errors and edge cases

- An unknown pack identifier or version is an internal policy wiring error.
- A missing required case component is an incomplete-check condition, not an invented finding.
- The engine never emits provider, transport, or storage errors.
- If the case contains only gaps, the engine may return an empty tuple; the caller decides whether
  that is sufficient for the current operation.

## Invariants

1. Deterministic checks are pure and repeatable.
2. The engine never reads ambient time, filesystem state, or provider output.
3. Same case + same pack = byte-equivalent ID-free candidates in the same order.
4. Deterministic findings never carry semantic provenance.
5. The work-integrity and research-evidence packs remain separate so one can evolve without
   silently changing the other.

## Tests

- `specs/tests/conformance.md` — deterministic findings from memory and SQLite adapters match.
- `specs/tests/unit.md` — rule-by-rule fixture coverage for each pack.
- `fixtures/policies/` — frozen case fixtures that prove the required findings and no-spurious
  findings cases.

## Open questions

None.
