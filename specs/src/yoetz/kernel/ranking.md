# src/yoetz/kernel/ranking.py — stable ordering and verdict selection for findings

**Wave:** B | **ADRs:** ADR-002, ADR-004, ADR-006 | **Imports (spec-tree):**
`domain/findings.md`, `protocol/coverage.md`, `protocol/errors.md` | **Imported by:**
`application/check.md`, `application/respond.md`, `domain/receipts.md`, `cli/render.md`

## Purpose

This file turns a pile of deterministic and semantic findings into the bounded, stable result that
the rest of Yoetz shows to users. It is the policy-aware ordering layer between raw findings
and the public check/receipt surface.

Ranking is not scoring. It does not average findings into a single number. It keeps the strongest
and most actionable findings first, preserves how many were suppressed, and carries the verdict that
the caller should present.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `RankedFindings` | frozen wrapper with ordered findings, suppressed count, verdict, and coverage |
| `CheckCompleteness` | enum `complete`, `coverage_incomplete`, `required_incomplete` |
| `RankingContext` | frozen dataclass `(coverage, completeness)` |
| `rank_findings(deterministic, semantic, context, max_findings)` | stable merge, selection, verdict, and coverage construction |

## Behavior

`rank_findings` takes the deterministic findings produced by the kernel, the semantic findings
returned by the model path, one explicit `RankingContext`, and the caller’s maximum finding count.
The application freezes `RankingContext` only after policy/semantic accounting is terminal:

- `coverage` is the component-wise weakest material coverage across the frozen case, every
  deterministic assessment/basis and candidate, every selected or rejected semantic dependency,
  every explicit unknown/redaction/freshness gap, and all findings before result capping;
- `required_incomplete` means a required deterministic pack failed, or `semantic_required`
  terminated without a valid semantic success when a material semantic case was required;
- `coverage_incomplete` means required work completed but a material dependency is
  missing/redacted/unknown/stale, or an optional requested semantic path terminated without usable
  evidence; and
- `complete` means every required deterministic pack completed, no material coverage gap remains,
  and the semantic path either succeeded or was not required/not material.

The context is a fact summary, not caller discretion: `application/check.md` owns the derivation
above and persists the underlying run/skipped/failed and semantic status/reason facts. The ranker
validates that `context.coverage` is no stronger than any candidate coverage.

It returns a `RankedFindings` value with:

- the selected findings ordered by materiality, actionability, evidence strength, and stable ID
  tie-break;
- the number of findings suppressed by the `max_findings` limit;
- the final verdict implied by the selection plus explicit completeness; and
- the exact weakest material coverage from `context`, independent of selection/capping.

The ordering rules are fixed:

1. registered priority `1` comes before `2`, which comes before `3`;
2. within a priority, kinds whose registered `actionable` trait is true come first;
3. the evidence-strength bucket sorts strongest first. It is the lexicographic pair of the
   zero-based ordinal of `coverage.artifact_observation` and then the zero-based ordinal of
   `coverage.evidence_immutability`, using the weakest-to-strongest enum orders in INTERFACES §5;
4. the coverage bucket sorts strongest first by the zero-based ordinal of
   `coverage.ledger_freshness`, then `coverage.authorship_assurance`, then whether at least one
   real check type (`deterministic` or `semantic_model_derived`) is present; fewer
   `known_gaps` sorts before more gaps. Publication-channel breadth and the identity of a gap or
   check-type set are not strength claims and do not otherwise affect ranking;
5. after those fact buckets tie, deterministic origin comes before semantic-model-derived origin;
6. only then do smaller unsigned ASCII `finding_id` bytes win as the final tie-break.

In implementation terms, `rank_key(finding)` is the ascending lexicographic tuple
`(priority, -actionable, -artifact_ordinal, -immutability_ordinal, -freshness_ordinal,
-authorship_ordinal, -real_check_present, known_gap_count, origin_ordinal, finding_id_bytes)`, where
`actionable` and `real_check_present` are `0|1`, and `origin_ordinal` is `0` for deterministic and
`1` for semantic-model-derived. There is no prose analysis, floating score, channel score, or
implementation-defined comparison. Candidate validation has already required `priority` to match
the kind's exact row in `FINDING_KIND_TRAITS`.

The canonical direct-to-agent merge
adds one bounded diversity rule after sorting: when `max_findings>=2` and at least one
post-validated semantic reviewer challenge has material priority 1 or 2, the selected set must
contain the highest-ranked such challenge. If the ordinary top-N selection omitted it, replace
that selection's lowest-ranked item, then sort the selected set by the same key. The displaced item becomes
suppressed while the challenge leaves the suppressed tail, so total suppression remains
`total_input_findings - returned_findings`. This reserves at most one slot; with
`max_findings=1`, or only priority-3 semantic
explanation, the ordinary top-N selection wins.

Deterministic findings outrank semantic findings when every registered fact bucket ties, because they are already
proven against the frozen projection state. Semantic findings can still appear earlier when they are
materially stronger by the same ordering rules. The one material-challenge slot does not relabel,
rewrite, or resolve a deterministic finding; every displaced deterministic finding remains in the
suppressed tail and the exact suppressed count is unchanged by the swap.

Verdict selection uses this exact precedence:

1. `context.completeness == required_incomplete` → `incomplete_check`, even when actionable
   findings are selected;
2. otherwise, any selected finding whose registered trait is `actionable=true` →
   `action_required`;
3. otherwise, `context.completeness == coverage_incomplete` → `insufficient_coverage`;
4. otherwise the selection MUST be empty and the verdict is `no_issue_detected`.

A `complete` context with a nonempty selection containing no actionable finding, or a
`coverage_incomplete` context whose recorded coverage has no corresponding material gap, is
invalid upstream. The only nonactionable kind in v0.1 is `ledger_stale_or_incomplete`, which necessarily accompanies
`coverage_incomplete` or `required_incomplete`; it can never produce `no_issue_detected`.

`max_findings` is a hard cap, not a hint. The ranker must never return more than the caller
requested and must never silently drop the suppressed-count information.

The returned coverage is always `context.coverage`. It includes all material inputs and every
candidate before capping, so suppressing or diversity-replacing a weak finding can never strengthen
the recorded check.

`RankedFindings` therefore carries four facts that must remain in sync: the ordered selection, the
suppressed-count tally, the verdict, and the full material coverage baseline. If any one of those
four is stale, the entire value is stale.

## Errors and edge cases

- `max_findings` must be positive and no larger than the registry limit.
- Duplicate `finding_id` values in the input are a broken invariant and must not be hidden.
- A finding with a malformed coverage vector is invalid upstream.
- A missing/malformed `RankingContext`, or a context coverage stronger than a material input, is
  invalid upstream.
- The ranker never rewrites finding text, provenance, or policy identity.
- The reviewer-voice rule applies only to post-validated semantic challenge findings and can reserve
  at most one slot.

## Invariants

1. Stable input order produces stable output order.
2. The ranker never fabricates or mutates findings.
3. Suppressed-count accounting is exact.
4. Verdicts never outrun the evidence or coverage.
5. Ranking is pure and side-effect free.
6. At any cap of at least two (including the default of three), one material accepted reviewer challenge reaches the main-agent finding
   surface without mutating deterministic truth.

## Tests

- `specs/tests/unit.md` — exhaustive kind-trait parity, each evidence/coverage sort component,
  origin and final-ID tie-breaks, diversity-slot behavior, and suppressed-count coverage.
- `specs/tests/conformance.md` — deterministic and semantic findings merge the same way in memory
  and SQLite runs.
- `specs/tests/unit/kernel/test_ranking.py.md` — inline immutable sort/verdict vectors; no separate
  finding-resource directory exists.
- `specs/fixtures/README.md` — finite adversarial/receipt cases exercising public ranked outcomes.

## Open questions

None.
